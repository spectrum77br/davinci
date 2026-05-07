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
)
from app.security.cipher import decrypt_json, encrypt_json
from app.services.marketplaces.base import SyncResult, SyncStatus
from app.services.marketplaces.factory import client_for
from app.services.marketplaces.ml import MercadoLivreClient
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


async def _resolve_listing(
    session: AsyncSession,
    *,
    integration_id: UUID,
    product_sku: str,
) -> Listing | None:
    """Find the ML listing for `(integration, sku)`. Phase 9b uses simple
    `(integration_id, sku)` match; richer routing (catalog id, variation)
    arrives in 9c with the catalog endpoint.
    """
    if not product_sku:
        return None
    return (
        await session.execute(
            select(Listing).where(
                and_(
                    Listing.integration_id == integration_id,
                    Listing.sku == product_sku,
                )
            ).limit(1)
        )
    ).scalar_one_or_none()


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
                    PricingAccount.user_id == user.id,
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
                    PricingProduct.user_id == user.id,
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
                    PricingOverride.user_id == user.id,
                )
            )
        )
    ).scalar_one_or_none()

    outcome: CalcOutcome = calculate(account, product, override)
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
                    Integration.user_id == user.id,
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
    if integration.platform != IntegrationPlatform.ML:
        return PushOutcome(
            ok=False,
            code="platform_not_implemented",
            detail=f"phase 9b ships ML only; got {integration.platform.value}",
            price=outcome.price,
        )

    listing = await _resolve_listing(
        session, integration_id=integration.id, product_sku=product.sku
    )
    if listing is None:
        return PushOutcome(
            ok=False,
            code="listing_not_found",
            detail=f"no listing for sku={product.sku} on integration={integration.id}",
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
    if not isinstance(client, MercadoLivreClient):
        return PushOutcome(
            ok=False,
            code="client_mismatch",
            detail=f"expected MLClient, got {type(client).__name__}",
        )

    result: SyncResult = await client.update_price(
        item_id=listing.external_id,
        price=float(outcome.price),
    )
    ok = result.status == SyncStatus.OK
    code = "ok" if ok else (result.error_code or result.status.value)
    response = PushOutcome(
        ok=ok,
        code=code,
        detail=result.error_detail,
        price=outcome.price,
        item_id=listing.external_id,
        variation_id=None,
        cached=False,
        payload={"calc": outcome.inputs or {}, "ml": result.payload},
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
