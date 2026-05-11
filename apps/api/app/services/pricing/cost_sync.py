"""Sync `pricing_products.bling_cost_price` from Bling (Fase 9d).

Walks every pricing_product owned by `user_id` whose SKU has a matching
`product.bling_product_id` (linked previously in Fase 3 import) and
calls Bling `GET /produtos/{id}` to grab `precoCusto`. Updates only when
the value differs to keep `updated_at` meaningful.

Runs in an Arq job; persists progress in `background_jobs`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import structlog
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BackgroundJob,
    BackgroundJobStatus,
    Integration,
    IntegrationPlatform,
    PricingProduct,
    Product,
)
from app.security.cipher import decrypt_json, encrypt_json
from app.services.marketplaces.bling import BlingClient

logger = structlog.get_logger()


def _now() -> datetime:
    return datetime.now(UTC)


def _to_decimal(v: object) -> Decimal | None:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v))
    except (ValueError, TypeError):
        return None


async def run_sync_bling_costs(
    session: AsyncSession,
    *,
    job_id: UUID,
    user_id: UUID,
) -> None:
    job = await session.get(BackgroundJob, job_id)
    if job is None:
        logger.error("cost_sync_job_missing", job_id=str(job_id))
        return

    job.status = BackgroundJobStatus.RUNNING
    job.started_at = _now()
    job.last_heartbeat_at = _now()
    await session.commit()

    integ = (
        await session.execute(
            select(Integration).where(
                and_(
                    Integration.user_id == user_id,
                    Integration.platform == IntegrationPlatform.BLING,
                )
            ).limit(1)
        )
    ).scalar_one_or_none()
    if integ is None:
        job.status = BackgroundJobStatus.FAILED
        job.error = "no_bling_integration"
        job.finished_at = _now()
        await session.commit()
        return

    creds = decrypt_json(integ.credentials)

    async def _persist(new_creds: dict) -> None:
        integ.credentials = encrypt_json(new_creds)
        exp = new_creds.get("expires_at")
        if exp:
            integ.token_expires_at = datetime.fromtimestamp(int(exp), tz=UTC)
        await session.commit()

    client = BlingClient(creds, on_token_refresh=_persist, integration_id=integ.id)

    # Index: product.sku → product.bling_product_id (only those linked).
    products = (
        await session.execute(
            select(Product).where(
                and_(
                    Product.user_id == user_id,
                    Product.bling_product_id.isnot(None),
                )
            )
        )
    ).scalars().all()
    by_sku = {p.sku: p for p in products}

    pricing_rows = (
        await session.execute(
            select(PricingProduct).where(PricingProduct.user_id == user_id)
        )
    ).scalars().all()

    candidates = [
        pp for pp in pricing_rows if pp.sku in by_sku
    ]
    job.total = len(candidates)
    job.processed = 0
    summary = {"total": len(candidates), "updated": 0, "skipped": 0, "errors": 0}
    await session.commit()

    for i, pp in enumerate(candidates):
        prod = by_sku[pp.sku]
        try:
            data = await client.get_product(prod.bling_product_id)
        except Exception as e:  # noqa: BLE001
            summary["errors"] += 1
            logger.warning(
                "cost_sync_get_product_failed", sku=pp.sku, err=str(e)
            )
            job.processed = i + 1
            continue

        new_cost = _to_decimal(data.get("precoCusto") or data.get("preco_custo"))
        if new_cost is None:
            summary["skipped"] += 1
        elif pp.bling_cost_price != new_cost:
            pp.bling_cost_price = new_cost
            summary["updated"] += 1
        else:
            summary["skipped"] += 1

        job.processed = i + 1
        if (i + 1) % 25 == 0:
            job.last_heartbeat_at = _now()
            await session.commit()

    job.status = BackgroundJobStatus.SUCCEEDED
    job.result = {"summary": summary}
    job.finished_at = _now()
    await session.commit()
