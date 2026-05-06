"""Auto-link orchestrator (Fase 3 scope).

For each user's Bling integration, scan products imported from Bling and ensure
there is a `product_links` row with platform=bling pointing to the same Bling
product (self-link). The Bling integration links every product to its origin
channel inside Bling and keeps the `store_id` derivation (integration -> store)
consistent with how Fase 4 will materialize ML/Shopee/Amazon links.

Non-Bling integrations are recorded in `details` as `no_adapter_yet` so the
job log is honest about what was skipped. Fase 4 fills those adapters.
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
)

logger = structlog.get_logger()


def _now() -> datetime:
    return datetime.now(UTC)


async def _heartbeat(session: AsyncSession, job: BackgroundJob) -> None:
    job.last_heartbeat_at = _now()
    await session.commit()


async def _append_detail(session: AsyncSession, job: BackgroundJob, entry: dict[str, Any]) -> None:
    entry = {"at": _now().isoformat(), **entry}
    job.details = [*(job.details or []), entry]
    await session.commit()


async def _link_bling_integration(
    session: AsyncSession,
    job: BackgroundJob,
    user_id: UUID,
    integ: Integration,
) -> tuple[int, int]:
    """Returns (created, already_present)."""
    products = (
        await session.execute(
            select(Product).where(
                and_(
                    Product.user_id == user_id,
                    Product.bling_product_id.is_not(None),
                )
            )
        )
    ).scalars().all()

    existing_links = (
        await session.execute(
            select(ProductLink).where(
                and_(
                    ProductLink.user_id == user_id,
                    ProductLink.integration_id == integ.id,
                    ProductLink.platform == IntegrationPlatform.BLING,
                )
            )
        )
    ).scalars().all()
    by_external = {
        (link.external_id, link.product_id): link for link in existing_links
    }

    created = 0
    already = 0
    for p in products:
        key = (str(p.bling_product_id), p.id)
        if key in by_external:
            already += 1
            continue
        link = ProductLink(
            user_id=user_id,
            product_id=p.id,
            integration_id=integ.id,
            store_id=integ.store_id,
            platform=IntegrationPlatform.BLING,
            external_id=str(p.bling_product_id),
            external_sku=p.sku,
            listing_title=p.name,
            stock=p.stock,
            price=p.price,
            last_sync_status=LinkSyncStatus.OK,
            last_sync_at=_now(),
        )
        session.add(link)
        created += 1
        job.processed = (job.processed or 0) + 1
        if created % 25 == 0:
            await _heartbeat(session, job)
    return created, already


async def run_auto_link(
    session: AsyncSession,
    *,
    job_id: UUID,
    user_id: UUID,
    integration_ids: list[UUID] | None,
) -> None:
    job = (
        await session.execute(select(BackgroundJob).where(BackgroundJob.id == job_id))
    ).scalar_one_or_none()
    if job is None:
        logger.error("auto_link_job_missing", job_id=str(job_id))
        return

    job.status = BackgroundJobStatus.RUNNING
    job.started_at = _now()
    job.last_heartbeat_at = _now()

    stmt = select(Integration).where(Integration.user_id == user_id)
    if integration_ids:
        stmt = stmt.where(Integration.id.in_(integration_ids))
    integrations = (await session.execute(stmt)).scalars().all()

    job.total = sum(1 for _ in integrations) or 1
    await session.commit()

    summary = {"created": 0, "already_present": 0, "skipped": 0}

    try:
        for integ in integrations:
            await _heartbeat(session, job)
            if integ.platform == IntegrationPlatform.BLING:
                created, already = await _link_bling_integration(session, job, user_id, integ)
                summary["created"] += created
                summary["already_present"] += already
                await _append_detail(
                    session,
                    job,
                    {
                        "integration_id": str(integ.id),
                        "platform": integ.platform.value,
                        "result": "ok",
                        "created": created,
                        "already_present": already,
                    },
                )
            else:
                summary["skipped"] += 1
                await _append_detail(
                    session,
                    job,
                    {
                        "integration_id": str(integ.id),
                        "platform": integ.platform.value,
                        "result": "no_adapter_yet",
                        "note": "marketplace adapter implemented in Phase 4",
                    },
                )
        job.status = BackgroundJobStatus.SUCCEEDED
        job.result = summary
    except Exception as e:  # noqa: BLE001
        logger.exception("auto_link_failed", job_id=str(job_id))
        job.status = BackgroundJobStatus.FAILED
        job.error = f"{type(e).__name__}: {e}"[:1000]
    finally:
        job.finished_at = _now()
        await session.commit()
