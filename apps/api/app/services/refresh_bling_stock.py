"""Refresh-stock-only job — manual quick path.

Iterates every Bling integration the user owns, paginates `/produtos`
100-at-a-time, and writes only `stock` (and `min_stock` when present) to
local `products` + `product_links`. No marketplace push, no full
orchestrator pipeline.

Use case: user wants fresh Bling stock without paying the full sync_all
cost (which also touches every marketplace link per product).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BackgroundJob,
    BackgroundJobStatus,
    Integration,
    IntegrationPlatform,
    LinkSyncStatus,
    Product,
    ProductLink,
)
from app.security.cipher import decrypt_json, encrypt_json
from app.services.marketplaces.bling import (
    BLING_PRODUCTS_PAGE_SIZE,
    BlingClient,
    parse_bling_product,
)

logger = structlog.get_logger()

DETAILS_MAX = 500


def _now() -> datetime:
    return datetime.now(UTC)


async def _append_detail(session: AsyncSession, job: BackgroundJob, entry: dict[str, Any]) -> None:
    entry = {"at": _now().isoformat(), **entry}
    current = list(job.details or [])
    current.append(entry)
    if len(current) > DETAILS_MAX:
        current = current[-DETAILS_MAX:]
    job.details = current
    job.last_heartbeat_at = _now()
    await session.commit()


async def _build_client(session: AsyncSession, integ: Integration) -> BlingClient:
    creds = decrypt_json(integ.credentials)

    async def _persist(new_creds: dict, _it=integ, _s=session) -> None:
        _it.credentials = encrypt_json(new_creds)
        exp = new_creds.get("expires_at")
        if exp:
            _it.token_expires_at = datetime.fromtimestamp(int(exp), tz=UTC)
        await _s.commit()

    return BlingClient(creds, on_token_refresh=_persist, integration_id=integ.id)


async def run_refresh_bling_stock(
    session: AsyncSession,
    *,
    job_id: UUID,
) -> None:
    job = await session.get(BackgroundJob, job_id)
    if job is None:
        logger.error("refresh_bling_stock_job_missing", job_id=str(job_id))
        return

    job.status = BackgroundJobStatus.RUNNING
    job.started_at = _now()
    job.last_heartbeat_at = _now()
    await session.commit()

    integrations = (
        await session.execute(
            select(Integration).where(Integration.platform == IntegrationPlatform.BLING)
        )
    ).scalars().all()

    if not integrations:
        job.status = BackgroundJobStatus.SUCCEEDED
        job.result = {"updated": 0, "missing_local": 0, "pages": 0, "integrations": 0}
        job.finished_at = _now()
        await session.commit()
        return

    summary = {
        "updated": 0,
        "missing_local": 0,
        "pages": 0,
        "integrations": len(integrations),
    }

    # Match Bling products to local products by Product.bling_product_id
    # (canonical), not by ProductLink — many tenants don't maintain a Bling
    # self-link and rely on the column instead.
    products = (
        await session.execute(
            select(Product).where(Product.bling_product_id.is_not(None))
        )
    ).scalars().all()
    product_by_bpid = {int(p.bling_product_id): p for p in products if p.bling_product_id is not None}

    try:
        for integ in integrations:
            await _append_detail(
                session,
                job,
                {
                    "integration_id": str(integ.id),
                    "phase": "start",
                    "platform": "bling",
                },
            )
            client = await _build_client(session, integ)

            bling_links = (
                await session.execute(
                    select(ProductLink).where(
                        ProductLink.integration_id == integ.id,
                        ProductLink.platform == IntegrationPlatform.BLING,
                    )
                )
            ).scalars().all()
            bling_link_by_external = {str(l.external_id): l for l in bling_links}

            page = 1
            while True:
                items = await client.list_products_page(
                    pagina=page, limite=BLING_PRODUCTS_PAGE_SIZE
                )
                if not items:
                    break

                page_updated = 0
                page_missing = 0
                for raw in items:
                    parsed = parse_bling_product(raw)
                    bpid = parsed.get("bling_product_id")
                    new_stock = parsed.get("stock")
                    if bpid is None or new_stock is None:
                        continue
                    product = product_by_bpid.get(int(bpid))
                    if product is None:
                        page_missing += 1
                        continue
                    product.stock = int(new_stock)
                    if parsed.get("min_stock") is not None:
                        product.min_stock = int(parsed["min_stock"])
                    # Backfill situacao/formato em produtos pré-existentes
                    # que ainda têm NULL (criados antes do fix do parse).
                    # Não sobrescrevemos valor já populado pra preservar
                    # qualquer correção manual feita no DB.
                    if product.situacao is None and parsed.get("situacao"):
                        product.situacao = parsed["situacao"]
                    if product.formato is None and parsed.get("formato"):
                        product.formato = parsed["formato"]
                    bling_link = bling_link_by_external.get(str(bpid))
                    if bling_link is not None:
                        bling_link.stock = int(new_stock)
                        bling_link.last_sync_status = LinkSyncStatus.OK
                        bling_link.last_sync_at = _now()
                        bling_link.last_error = None
                    page_updated += 1

                summary["updated"] += page_updated
                summary["missing_local"] += page_missing
                summary["pages"] += 1
                job.processed = (job.processed or 0) + len(items)
                if job.total < job.processed:
                    job.total = job.processed
                await _append_detail(
                    session,
                    job,
                    {
                        "integration_id": str(integ.id),
                        "page": page,
                        "fetched": len(items),
                        "updated": page_updated,
                        "missing_local": page_missing,
                    },
                )

                if len(items) < BLING_PRODUCTS_PAGE_SIZE:
                    break
                page += 1

        job.status = BackgroundJobStatus.SUCCEEDED
        job.result = summary
    except Exception as e:  # noqa: BLE001
        logger.exception("refresh_bling_stock_failed", job_id=str(job_id))
        job.status = BackgroundJobStatus.FAILED
        job.error = f"{type(e).__name__}: {e}"[:1000]
        job.result = summary
    finally:
        job.finished_at = _now()
        await session.commit()
