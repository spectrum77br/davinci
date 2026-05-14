"""Pricing push service (Fase 9b).

Resolves an `(account, product)` pair to a target ML listing, computes the
price via `pricing.calc.calculate`, and dispatches `update_price` on the
right marketplace client. Handles `Idempotency-Key` deduplication backed by
the `pricing_push_idempotency` table (resolves B13).

Phase 9b implements ML only. Shopee/Amazon arrive in 9c; until then any push
to a non-ML account returns HTTP 501 (mapped from this service's outcome).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

import structlog
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Integration,
    IntegrationPlatform,
    Listing,
    PricingAccount,
    PricingOverride,
    PricingProduct,
    PricingPushIdempotency,
    Product,
    ProductLink,
    Segment,
)
from app.deps.auth import user_scope
from app.security.cipher import decrypt_json, encrypt_json
from app.services.marketplaces.base import SyncResult, SyncStatus
from app.services.marketplaces.factory import client_for
from app.services.marketplaces.ml import MercadoLivreClient
from app.services.marketplaces.shopee import ShopeeClient
from app.services.marketplaces.amazon import AmazonClient
from app.services.marketplaces.tiktok import TikTokClient
from app.services.marketplaces.temu import TemuClient
from app.services.pricing.calc import CalcOutcome, calculate

if TYPE_CHECKING:
    from app.models import User

logger = structlog.get_logger()

IDEMPOTENCY_TTL = timedelta(hours=24)


@dataclass(slots=True)
class PushOutcome:
    ok: bool
    code: str
    detail: str | None = None
    price: Decimal | None = None
    item_id: str | None = None
    variation_id: str | None = None
    cached: bool = False
    payload: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "code": self.code,
            "detail": self.detail,
            "price": str(self.price) if self.price is not None else None,
            "item_id": self.item_id,
            "variation_id": self.variation_id,
            "cached": self.cached,
            "payload": self.payload,
        }


def _hash_request(
    account_id: UUID, product_id: UUID, price: Decimal | None
) -> str:
    payload = json.dumps(
        {
            "a": str(account_id),
            "p": str(product_id),
            "v": str(price) if price is not None else None,
        },
        sort_keys=True,
    ).encode()
    return hashlib.sha256(payload).hexdigest()


async def _lookup_idempotent(
    session: AsyncSession,
    key: str,
    user_id: UUID,
    request_hash: str,
) -> dict | None:
    row = (
        await session.execute(
            select(PricingPushIdempotency).where(
                PricingPushIdempotency.key == key
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    now = datetime.now(UTC)
    if row.expires_at <= now:
        await session.delete(row)
        await session.flush()
        return None
    if row.user_id != user_id:
        # Same key, different tenant — refuse implicitly via "code mismatch" up the stack.
        return {"_conflict": "user_mismatch"}
    if row.request_hash != request_hash:
        return {"_conflict": "request_mismatch"}
    return row.response


async def _store_idempotent(
    session: AsyncSession,
    key: str,
    user_id: UUID,
    request_hash: str,
    response: dict,
) -> None:
    now = datetime.now(UTC)
    session.add(
        PricingPushIdempotency(
            key=key,
            user_id=user_id,
            request_hash=request_hash,
            response=response,
            created_at=now,
            expires_at=now + IDEMPOTENCY_TTL,
        )
    )
    await session.flush()


async def _resolve_product_type(
    session: AsyncSession, product: PricingProduct
) -> int | None:
    """Returns the product_type (1..5) for a pricing_product by looking up
    the leaf segment's sort_order under its root department. None if the
    segment chain doesn't resolve.
    """
    leaf = (
        await session.execute(
            select(Segment).where(Segment.id == product.segment_id)
        )
    ).scalar_one_or_none()
    if leaf is None or leaf.parent_id is None:
        return None
    return int(leaf.sort_order or 0) + 1


async def _resolve_product_links(
    session: AsyncSession,
    *,
    user_id: UUID,
    integration_id: UUID,
    pricing_product_sku: str,
) -> list[ProductLink]:
    """Find all product_links for the (integration, pricing_product) pair.

    SSH semantics: pricing_product.sku is a comma-joined list of variant SKUs
    (e.g. "x043,x044,x045,x046"). Each variant maps to a row in
    `davinci.products`, and each `products` row can have multiple
    `product_links` rows (one per integration). We return every link for
    every variant SKU on the target integration so the caller can push the
    price to all variations of the listing.
    """
    if not pricing_product_sku:
        return []
    sku_pieces = [s.strip() for s in pricing_product_sku.split(",") if s.strip()]
    if not sku_pieces:
        return []
    product_ids = (
        await session.execute(
            select(Product.id).where(
                and_(
                    Product.user_id == user_id,
                    Product.sku.in_(sku_pieces),
                )
            )
        )
    ).scalars().all()
    if not product_ids:
        return []
    return list(
        (
            await session.execute(
                select(ProductLink).where(
                    and_(
                        ProductLink.user_id == user_id,
                        ProductLink.integration_id == integration_id,
                        ProductLink.product_id.in_(product_ids),
                    )
                )
            )
        ).scalars().all()
    )


async def push_one(
    session: AsyncSession,
    *,
    user: User,
    account_id: UUID,
    product_id: UUID,
    idempotency_key: str | None = None,
) -> PushOutcome:
    account = (
        await session.execute(
            select(PricingAccount).where(
                and_(
                    PricingAccount.id == account_id,
                    user_scope(PricingAccount, user),
                )
            )
        )
    ).scalar_one_or_none()
    if account is None:
        return PushOutcome(ok=False, code="account_not_found")

    product = (
        await session.execute(
            select(PricingProduct).where(
                and_(
                    PricingProduct.id == product_id,
                    user_scope(PricingProduct, user),
                )
            )
        )
    ).scalar_one_or_none()
    if product is None:
        return PushOutcome(ok=False, code="product_not_found")

    override = (
        await session.execute(
            select(PricingOverride).where(
                and_(
                    PricingOverride.pricing_account_id == account_id,
                    PricingOverride.pricing_product_id == product_id,
                    user_scope(PricingOverride, user),
                )
            )
        )
    ).scalar_one_or_none()

    product_type = await _resolve_product_type(session, product)
    outcome: CalcOutcome = calculate(account, product, override, product_type)
    if outcome.source in {"disabled", "locked"} and (
        outcome.source == "disabled" or outcome.price is None
    ):
        return PushOutcome(
            ok=False,
            code=f"cell_{outcome.source}",
            detail=outcome.detail,
            price=outcome.price,
        )
    if outcome.price is None:
        return PushOutcome(
            ok=False,
            code="missing_inputs",
            detail=outcome.detail,
            payload={"inputs": outcome.inputs or {}},
        )

    request_hash = _hash_request(account_id, product_id, outcome.price)
    if idempotency_key:
        cached = await _lookup_idempotent(
            session, idempotency_key, user.id, request_hash
        )
        if cached is not None:
            if "_conflict" in cached:
                return PushOutcome(
                    ok=False,
                    code="idempotency_conflict",
                    detail=cached["_conflict"],
                )
            return PushOutcome(
                ok=bool(cached.get("ok")),
                code=str(cached.get("code") or "ok"),
                detail=cached.get("detail"),
                price=Decimal(cached["price"]) if cached.get("price") else None,
                item_id=cached.get("item_id"),
                variation_id=cached.get("variation_id"),
                cached=True,
                payload=cached.get("payload") or {},
            )

    if account.integration_id is None:
        return PushOutcome(
            ok=False,
            code="account_not_linked",
            detail="pricing_account.integration_id is null",
            price=outcome.price,
        )

    integration = (
        await session.execute(
            select(Integration).where(
                and_(
                    Integration.id == account.integration_id,
                    user_scope(Integration, user),
                )
            )
        )
    ).scalar_one_or_none()
    if integration is None:
        return PushOutcome(
            ok=False,
            code="integration_not_found",
            price=outcome.price,
        )
    # All platforms are now supported
    _SUPPORTED_PLATFORMS = {
        IntegrationPlatform.ML,
        IntegrationPlatform.SHOPEE,
        IntegrationPlatform.AMAZON,
        IntegrationPlatform.TIKTOK,
        IntegrationPlatform.TEMU,
    }
    if integration.platform not in _SUPPORTED_PLATFORMS:
        return PushOutcome(
            ok=False,
            code="platform_not_implemented",
            detail=f"platform {integration.platform.value} not supported for pricing push",
            price=outcome.price,
        )

    links = await _resolve_product_links(
        session,
        user_id=user.id,
        integration_id=integration.id,
        pricing_product_sku=product.sku,
    )
    if not links:
        return PushOutcome(
            ok=False,
            code="listing_not_found",
            detail=f"no product_links for sku={product.sku} on integration={integration.id}",
            price=outcome.price,
        )

    creds = decrypt_json(integration.credentials)

    async def _persist_refresh(new_creds: dict) -> None:
        integration.credentials = encrypt_json(new_creds)
        exp = new_creds.get("expires_at")
        if exp:
            integration.token_expires_at = datetime.fromtimestamp(int(exp), tz=UTC)
        await session.commit()

    client = client_for(
        integration.platform, creds, on_token_refresh=_persist_refresh
    )

    # Push to every variation/link. Aggregate the outcome: ok if all ok,
    # partial if some ok some not, fail if none ok. Item/variation id of
    # the response is the first successful (or first attempted) link.
    per_link: list[dict] = []
    ok_count = 0
    fail_count = 0
    first_item: str | None = None
    first_variation: str | None = None
    last_error_code: str | None = None
    last_error_detail: str | None = None
    for link in links:
        if first_item is None:
            first_item = link.external_id
            first_variation = link.variation_id
        result = await _dispatch_price_update_link(
            client, integration.platform, link, float(outcome.price)
        )
        per_link.append(
            {
                "external_id": link.external_id,
                "variation_id": link.variation_id,
                "status": result.status.value,
                "error_code": result.error_code,
            }
        )
        if result.status == SyncStatus.OK:
            ok_count += 1
            if first_item != link.external_id:
                # If a later link succeeded but earlier failed, surface a
                # working item_id back to the UI.
                first_item = link.external_id
                first_variation = link.variation_id
        else:
            fail_count += 1
            last_error_code = result.error_code or result.status.value
            last_error_detail = result.error_detail

    if ok_count == len(links):
        agg_ok = True
        agg_code = "ok"
        agg_detail = None
    elif ok_count > 0:
        agg_ok = True
        agg_code = "partial"
        agg_detail = f"{ok_count}/{len(links)} variations ok; last error: {last_error_code}"
    else:
        agg_ok = False
        agg_code = last_error_code or "push_failed"
        agg_detail = last_error_detail

    response = PushOutcome(
        ok=agg_ok,
        code=agg_code,
        detail=agg_detail,
        price=outcome.price,
        item_id=first_item,
        variation_id=first_variation,
        cached=False,
        payload={"calc": outcome.inputs or {}, "links": per_link},
    )

    if idempotency_key:
        await _store_idempotent(
            session,
            idempotency_key,
            user.id,
            request_hash,
            response.to_dict(),
        )
    return response


async def _dispatch_price_update_link(
    client,
    platform: IntegrationPlatform,
    link: "ProductLink",
    price: float,
) -> SyncResult:
    """Route price update to the correct client method based on platform,
    using ProductLink as the source of (external_id, variation_id, sku)."""
    try:
        if platform == IntegrationPlatform.ML:
            return await client.update_price(
                item_id=link.external_id,
                price=price,
                variation_id=link.variation_id,
            )
        elif platform == IntegrationPlatform.SHOPEE:
            return await client.update_price(
                item_id=link.external_id,
                price=price,
                variation_id=link.variation_id,
            )
        elif platform == IntegrationPlatform.AMAZON:
            sku = link.external_sku or link.external_id
            return await client.update_price(sku=sku, price=price)
        elif platform == IntegrationPlatform.TIKTOK:
            sku_id = link.variation_id or ""
            try:
                await client.update_price_with_activation(
                    product_id=link.external_id,
                    sku_ids=sku_id,
                    price=price,
                )
                return SyncResult(
                    status=SyncStatus.OK,
                    payload={"product_id": link.external_id, "sku_id": sku_id, "price": price},
                )
            except RuntimeError as e:
                return SyncResult(
                    status=SyncStatus.FATAL,
                    error_code="tiktok_price_error",
                    error_detail=str(e)[:500],
                )
        elif platform == IntegrationPlatform.TEMU:
            product_sku_id = link.variation_id or link.external_id
            try:
                await client.update_price(
                    product_sku_id=product_sku_id,
                    price=price,
                )
                return SyncResult(
                    status=SyncStatus.OK,
                    payload={"product_sku_id": product_sku_id, "price": price},
                )
            except RuntimeError as e:
                return SyncResult(
                    status=SyncStatus.FATAL,
                    error_code="temu_price_error",
                    error_detail=str(e)[:500],
                )
        else:
            return SyncResult(
                status=SyncStatus.FATAL,
                error_code="platform_not_implemented",
                error_detail=f"No price push for {platform.value}",
            )
    except Exception as e:  # noqa: BLE001
        logger.error(
            "pricing_push_dispatch_error",
            platform=platform.value,
            listing_id=str(listing.id),
            error=str(e)[:500],
        )
        return SyncResult(
            status=SyncStatus.FATAL,
            error_code="push_dispatch_error",
            error_detail=str(e)[:500],
        )
