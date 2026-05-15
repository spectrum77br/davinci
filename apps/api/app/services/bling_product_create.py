"""Auto-create a local Product from Bling when a stock/product webhook
arrives for a Bling product we never imported. Single-tenant: the user
is attributed by the caller (typically the owner of the Bling
integration). No marketplace links are created — a follow-up webhook or
manual link is required for that."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Integration,
    IntegrationPlatform,
    Product,
)
from app.security.cipher import decrypt_json, encrypt_json
from app.services.marketplaces.bling import BlingClient, parse_bling_product

logger = structlog.get_logger()


async def _bling_client_for_user(session: AsyncSession) -> BlingClient | None:
    integ = (
        await session.execute(
            select(Integration).where(
                Integration.platform == IntegrationPlatform.BLING
            ).limit(1)
        )
    ).scalar_one_or_none()
    if integ is None:
        return None
    creds = decrypt_json(integ.credentials)

    async def _persist(new_creds: dict) -> None:
        integ.credentials = encrypt_json(new_creds)
        exp = new_creds.get("expires_at")
        if exp:
            integ.token_expires_at = datetime.fromtimestamp(int(exp), tz=UTC)
        await session.commit()

    return BlingClient(creds, on_token_refresh=_persist, integration_id=integ.id)


async def run_auto_create_product_from_bling(
    session: AsyncSession,
    *,
    bling_product_id: int,
    user_id: UUID,
) -> dict[str, Any]:
    existing = (
        await session.execute(
            select(Product).where(
                Product.bling_product_id == bling_product_id
            ).limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return {"ok": True, "created": False, "product_id": str(existing.id)}

    client = await _bling_client_for_user(session)
    if client is None:
        logger.warning(
            "auto_create_product_no_bling_integration",
            user_id=str(user_id),
            bling_product_id=bling_product_id,
        )
        return {"ok": False, "error": "no_bling_integration"}

    try:
        raw = await client.get_product(bling_product_id)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "auto_create_product_fetch_failed",
            bling_product_id=bling_product_id,
            err=str(e),
        )
        return {"ok": False, "error": "fetch_failed"}

    if not raw:
        return {"ok": False, "error": "empty_product"}

    parsed = parse_bling_product(raw)
    sku = parsed.get("sku")
    if not sku:
        logger.warning(
            "auto_create_product_missing_sku",
            bling_product_id=bling_product_id,
        )
        return {"ok": False, "error": "missing_sku"}

    by_sku = (
        await session.execute(
            select(Product).where(Product.sku == sku).limit(1)
        )
    ).scalar_one_or_none()
    if by_sku is not None:
        if by_sku.bling_product_id is None:
            by_sku.bling_product_id = parsed["bling_product_id"]
            await session.commit()
        return {"ok": True, "created": False, "product_id": str(by_sku.id)}

    product = Product(
        user_id=user_id,
        sku=sku,
        name=parsed.get("name") or sku,
        bling_product_id=parsed["bling_product_id"],
        stock=int(parsed.get("stock") or 0),
        min_stock=int(parsed.get("min_stock") or 0),
        price=parsed.get("price"),
        bling_cost_price=parsed.get("bling_cost_price"),
        image_url=parsed.get("image_url"),
        category=parsed.get("category"),
        observation=parsed.get("observation"),
    )
    session.add(product)
    await session.commit()
    logger.info(
        "auto_create_product_done",
        product_id=str(product.id),
        sku=sku,
        bling_product_id=parsed["bling_product_id"],
    )
    return {"ok": True, "created": True, "product_id": str(product.id)}
