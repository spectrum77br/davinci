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
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BackgroundJob,
    BackgroundJobStatus,
    Integration,
    IntegrationPlatform,
    LinkSyncStatus,
    Product,
    ProductLink,
    User,
    UserRole,
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

    return BlingClient(creds, on_token_refresh=_persist)


async def run_refresh_bling_stock(
    session: AsyncSession,
    *,
    job_id: UUID,
    user_id: UUID,
) -> None:
    job = await session.get(BackgroundJob, job_id)
    if job is None:
        logger.error("refresh_bling_stock_job_missing", job_id=str(job_id))
        return

    job.status = BackgroundJobStatus.RUNNING
    job.started_at = _now()
    job.last_heartbeat_at = _now()
    await session.commit()

    user = await session.get(User, user_id)
    is_admin = user is not None and user.role == UserRole.ADMIN

    integ_where = [Integration.platform == IntegrationPlatform.BLING]
    if not is_admin:
        integ_where.append(Integration.user_id == user_id)
    integrations = (
        await session.execute(select(Integration).where(and_(*integ_where)))
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

            link_where = [
                ProductLink.integration_id == integ.id,
                ProductLink.platform == IntegrationPlatform.BLING,
            ]
            if not is_admin:
                link_where.append(ProductLink.user_id == user_id)
            existing_links_q = await session.execute(
                select(ProductLink).where(and_(*link_where))
            )
            link_by_external = {
                str(l.external_id): l for l in existing_links_q.scalars().all()
            }

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
                    link = link_by_external.get(str(bpid))
                    if link is None:
                        page_missing += 1
                        continue
                    link.stock = int(new_stock)
                    link.last_sync_status = LinkSyncStatus.OK
                    link.last_sync_at = _now()
                    link.last_error = None
                    product = await session.get(Product, link.product_id)
                    if product is not None:
                        product.stock = int(new_stock)
                        if parsed.get("min_stock") is not None:
                            product.min_stock = int(parsed["min_stock"])
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
