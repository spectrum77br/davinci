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
    CellStatus,
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
from app.services.pricing.sku_match import (
    build_prefix_index,
    dedup_links_for_push,
    filter_products_by_department,
    ml_listing_type_for_account,
    resolve_product_ids,
)

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


async def _resolve_product_links_for_push(
    session: AsyncSession,
    *,
    account: PricingAccount,
    pricing_product: PricingProduct,
    department: str | None,
    platform_str: str,
) -> list[ProductLink]:
    """SSH-style link resolution for a push target.

    Walks the prefix index to get every davinci.products row that *could*
    map to the pricing_product, then narrows with the department-specific
    rule (celular = kit-only by base, mala = exact main SKU, catalogo =
    exact non-kit), filters by ML listing_type when the account is
    classico/premium, and dedups per platform semantics.
    """
    if not pricing_product.sku:
        return []
    rows = (
        await session.execute(select(Product.id, Product.sku))
    ).all()
    # Stage 1: prefix index narrows the candidate set so we don't carry
    # every product through the department filter.
    prefix_index = build_prefix_index((pid, sku) for pid, sku in rows)
    candidate_ids = set(resolve_product_ids(pricing_product.sku, prefix_index))
    if not candidate_ids:
        return []
    candidate_products = [(pid, sku) for pid, sku in rows if pid in candidate_ids]
    # Stage 2: SSH department rule. For celular, kit-only by base SKU; for
    # mala, exact main SKU; for catalogo, exact non-kit SKU.
    target_ids = filter_products_by_department(
        department or "celular",
        pricing_product.sku,
        candidate_products,
        platform=platform_str,
    )
    if not target_ids:
        return []
    # Stage 3: load product_links scoped to (integration, target products),
    # then apply ML listing_type filter and platform-specific dedup.
    links_q = select(ProductLink).where(
        and_(
            ProductLink.integration_id == account.integration_id,
            ProductLink.product_id.in_(target_ids),
        )
    )
    links = list((await session.execute(links_q)).scalars().all())
    if platform_str == "ml":
        wanted_listing_type = ml_listing_type_for_account(account.listing_type)
        if wanted_listing_type:
            links = [lk for lk in links if (lk.listing_type or "") == wanted_listing_type]
    return dedup_links_for_push(platform_str, links)


async def _persist_cell_status(
    session: AsyncSession,
    *,
    user_id: UUID,
    account_id: UUID,
    product_id: UUID,
    new_status: CellStatus,
    existing_override: PricingOverride | None,
) -> None:
    """Write the post-push cell_status to pricing_overrides.

    Skip writes when the cell carries a user-explicit state (manual / locked /
    NA / disabled) — those win over auto/error/no_link from a push.

    SV and NO_LINK are *transient* "no product_link found" markers — push
    success clears them automatically so a cell that recovered a link
    (e.g. after auto-link) stops showing SV without manual intervention.
    """
    USER_EXPLICIT = {
        CellStatus.MANUAL,
        CellStatus.LOCKED,
        CellStatus.DISABLED,
        CellStatus.NA,
    }
    if existing_override is not None and existing_override.cell_status in USER_EXPLICIT:
        return
    if existing_override is None:
        # Only create a row when there's a non-auto state to persist; a fresh
        # cell with status='auto' doesn't need an override entry.
        if new_status == CellStatus.AUTO:
            return
        session.add(
            PricingOverride(
                user_id=user_id,
                pricing_product_id=product_id,
                pricing_account_id=account_id,
                price_override=None,
                cell_status=new_status,
            )
        )
    else:
        if existing_override.cell_status == new_status:
            return
        existing_override.cell_status = new_status
    await session.flush()


async def _account_department(
    session: AsyncSession, account: PricingAccount
) -> str | None:
    """Return the root segment slug for an account ("celular"/"mala"/...).
    pricing_accounts.segment_id is pinned to the root segment by the
    importer, so a single fetch is enough."""
    if account.segment_id is None:
        return None
    seg = (
        await session.execute(
            select(Segment).where(Segment.id == account.segment_id)
        )
    ).scalar_one_or_none()
    if seg is None:
        return None
    if seg.parent_id is None:
        return seg.slug
    parent = (
        await session.execute(
            select(Segment).where(Segment.id == seg.parent_id)
        )
    ).scalar_one_or_none()
    return parent.slug if parent and parent.parent_id is None else None


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

    # NA still BLOCKS push (user said "não anunciar"). SV is now transient —
    # we still let the push proceed; if no link exists the resolver below
    # will return no_link and the override updates accordingly. If a link
    # exists (auto-link recreated it), push succeeds and clears SV.
    if override is not None and override.cell_status == CellStatus.NA:
        return PushOutcome(
            ok=False,
            code="cell_na",
            detail="cell_status=NA blocks push",
            price=None,
        )

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

    department = await _account_department(session, account)
    links = await _resolve_product_links_for_push(
        session,
        account=account,
        pricing_product=product,
        department=department,
        platform_str=integration.platform.value,
    )
    if not links:
        await _persist_cell_status(
            session,
            user_id=user.id,
            account_id=account_id,
            product_id=product_id,
            new_status=CellStatus.NO_LINK,
            existing_override=override,
        )
        return PushOutcome(
            ok=False,
            code="no_link",
            detail=(
                f"no product_links for sku={product.sku} on integration="
                f"{integration.id} (dept={department!r}, listing_type="
                f"{account.listing_type!r})"
            ),
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

    # Pre-load the davinci.products.sku for every link so Amazon (which
    # addresses listings by seller SKU, not external_id) can use the real
    # value. SSH-imported links carry external_sku=NULL, hence the lookup.
    product_ids_in_links = list({lk.product_id for lk in links})
    sku_by_product: dict[UUID, str] = {}
    if product_ids_in_links:
        sku_rows = (
            await session.execute(
                select(Product.id, Product.sku).where(
                    Product.id.in_(product_ids_in_links)
                )
            )
        ).all()
        sku_by_product = {pid: sku for pid, sku in sku_rows if sku}

    # Push to every variation/link. Aggregate the outcome: ok if all ok,
    # partial if some ok some not, fail if none ok. Item/variation id of
    # the response is the first successful (or first attempted) link.
    per_link: list[dict] = []
    ok_count = 0
    skipped_count = 0
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
            client,
            integration.platform,
            link,
            float(outcome.price),
            product_sku=sku_by_product.get(link.product_id),
        )
        result = _reclassify_skipped(result)
        # SSH-style per-link entry (externalId/success/skipped/message) plus
        # the original status/error_code fields kept for back-compat. UI
        # surfaces `message` directly and uses `skipped` to bucket
        # "encerrado/moderação"-like outcomes as warnings, not errors.
        if result.status == SyncStatus.OK:
            link_message = f"R${int(outcome.price)}"
        elif result.error_detail:
            link_message = result.error_detail[:300]
        elif result.error_code:
            link_message = result.error_code
        else:
            link_message = result.status.value
        per_link.append(
            {
                "externalId": link.external_id,
                "variationId": link.variation_id,
                "success": result.status == SyncStatus.OK,
                "skipped": result.status == SyncStatus.SKIPPED,
                "message": link_message,
                # back-compat
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
        elif result.status == SyncStatus.SKIPPED:
            skipped_count += 1
        else:
            fail_count += 1
            last_error_code = result.error_code or result.status.value
            last_error_detail = result.error_detail

    if ok_count == len(links):
        agg_ok = True
        agg_code = "ok"
        agg_detail = None
        post_status = CellStatus.AUTO
    elif ok_count > 0:
        agg_ok = True
        agg_code = "partial"
        agg_detail = (
            f"{ok_count}/{len(links)} variations ok"
            + (f"; {skipped_count} pulado(s)" if skipped_count else "")
            + (f"; last error: {last_error_code}" if last_error_code else "")
        )
        # Partial: at least one variation got the new price. Treat the cell
        # as good ("auto"); the per-link payload still carries the failures
        # for the UI to surface.
        post_status = CellStatus.AUTO
    elif fail_count == 0 and skipped_count > 0:
        # All links were "encerrado/moderação" — not a hard failure. Keep
        # the cell as-is (no state change) and surface a warning code so
        # the UI can show it differently from an error.
        agg_ok = False
        agg_code = "all_skipped"
        agg_detail = f"{skipped_count} anúncio(s) pulado(s) (encerrado/moderação)"
        post_status = CellStatus.AUTO
    else:
        agg_ok = False
        agg_code = last_error_code or "push_failed"
        agg_detail = last_error_detail
        post_status = CellStatus.ERROR

    await _persist_cell_status(
        session,
        user_id=user.id,
        account_id=account_id,
        product_id=product_id,
        new_status=post_status,
        existing_override=override,
    )

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


# SSH parity: marketplaces sometimes reject price changes because the listing
# is closed/under moderation/ended. SSH classifies those outcomes as "pulado"
# (skipped) rather than hard errors so the UI summary doesn't paint the whole
# push red over inactive listings. We match the same Portuguese substrings the
# SSH router uses, plus the canonical ML error codes.
_SKIPPED_HINTS = (
    "encerrado",
    "encerrada",
    "moderação",
    "moderacao",
    "under_review",
    "listing_closed",
    "item.status.closed",
    "item_closed",
)


def _reclassify_skipped(result: SyncResult) -> SyncResult:
    if result.status != SyncStatus.FATAL:
        return result
    haystack = " ".join(
        str(part).lower()
        for part in (result.error_code, result.error_detail)
        if part
    )
    if not haystack:
        return result
    if any(h in haystack for h in _SKIPPED_HINTS):
        return SyncResult(
            status=SyncStatus.SKIPPED,
            error_code=result.error_code or "listing_closed_or_moderation",
            error_detail=result.error_detail,
            qty_before=result.qty_before,
            qty_after=result.qty_after,
            payload=result.payload,
        )
    return result


async def _dispatch_price_update_link(
    client,
    platform: IntegrationPlatform,
    link: "ProductLink",
    price: float,
    *,
    product_sku: str | None = None,
) -> SyncResult:
    """Route price update to the correct client method based on platform,
    using ProductLink as the source of (external_id, variation_id) and
    `product_sku` (the linked davinci.products.sku) for Amazon — which
    addresses listings by seller SKU, NOT external_id."""
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
            # SSH spec: Amazon addresses listings by seller SKU. Prefer the
            # actual product SKU; fall back to external_sku then external_id
            # only when the join didn't resolve.
            sku = product_sku or link.external_sku or link.external_id
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
            link_id=str(link.id),
            external_id=link.external_id,
            error=str(e)[:500],
        )
        return SyncResult(
            status=SyncStatus.FATAL,
            error_code="push_dispatch_error",
            error_detail=str(e)[:500],
        )
