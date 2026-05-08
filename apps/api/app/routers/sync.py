"""Sync router (Fase 4a).

Endpoints:
    POST /api/jobs/sync-all       — enqueue full sync run
    POST /api/sync/product/{id}   — sync one product (synchronous)
    GET  /api/sync-logs           — paginated logs
    GET  /api/sync-logs/stats     — last-window aggregates
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_active_user, require_permission, user_scope
from app.models import (
    BackgroundJob,
    BackgroundJobStatus,
    BackgroundJobType,
    LinkSyncStatus,
    Product,
    SyncLog,
    User,
)
from app.schemas.products import JobCreatedOut, JobOut
from app.schemas.sync import (
    SyncAllIn,
    SyncLogOut,
    SyncLogPage,
    SyncLogStats,
)
from app.services.advisory_lock import try_user_sync_lock
from app.services.sync_orchestrator import SyncOrchestrator
from app.worker_pool import get_arq_pool

logger = structlog.get_logger()
router = APIRouter(prefix="/api", tags=["sync"])


@router.post(
    "/jobs/sync-all",
    response_model=JobCreatedOut,
    status_code=status.HTTP_201_CREATED,
)
async def enqueue_sync_all(
    body: SyncAllIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("produtos", "edit"))],
) -> JobCreatedOut:
    job = BackgroundJob(
        type=BackgroundJobType.SYNC_ALL,
        status=BackgroundJobStatus.PENDING,
        created_by=user.id,
        payload={
            "integration_ids": [str(i) for i in (body.integration_ids or [])],
            "product_ids": [str(p) for p in (body.product_ids or [])],
        },
    )
    session.add(job)
    await session.flush()

    pool = await get_arq_pool()
    arq = await pool.enqueue_job(
        "sync_all_run",
        str(job.id),
        str(user.id),
        body.product_ids and [str(p) for p in body.product_ids],
    )
    if arq is not None:
        job.arq_job_id = arq.job_id
    await session.commit()
    return JobCreatedOut(job_id=job.id)


@router.post(
    "/jobs/backfill-ml-stock",
    response_model=JobCreatedOut,
    status_code=status.HTTP_201_CREATED,
)
async def enqueue_backfill_ml_stock(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("produtos", "edit"))],
) -> JobCreatedOut:
    """One-shot repair (B2): re-fetch ML stock for links with stock=0 or
    last_sync_at=NULL. No outbound push."""
    job = BackgroundJob(
        type=BackgroundJobType.BACKFILL_ML_STOCK,
        status=BackgroundJobStatus.PENDING,
        created_by=user.id,
        payload={},
    )
    session.add(job)
    await session.flush()

    pool = await get_arq_pool()
    arq = await pool.enqueue_job("ml_backfill_run", str(job.id), str(user.id))
    if arq is not None:
        job.arq_job_id = arq.job_id
    await session.commit()
    return JobCreatedOut(job_id=job.id)


@router.post("/sync/product/{product_id}", response_model=JobOut)
async def sync_product(
    product_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("produtos", "edit"))],
) -> JobOut:
    product = (
        await session.execute(
            select(Product).where(
                and_(Product.id == product_id, user_scope(Product, user))
            )
        )
    ).scalar_one_or_none()
    if product is None:
        raise HTTPException(404, detail={"code": "product_not_found"})

    job = BackgroundJob(
        type=BackgroundJobType.SYNC_PRODUCT,
        status=BackgroundJobStatus.PENDING,
        created_by=user.id,
        payload={"product_id": str(product_id)},
    )
    session.add(job)
    await session.flush()

    async with try_user_sync_lock(session, user.id) as acquired:
        if not acquired:
            raise HTTPException(
                409,
                detail={"code": "sync_already_running"},
            )
        orch = SyncOrchestrator(session, user_id=user.id, job=job)
        await orch.run([product])

    return JobOut.model_validate(job, from_attributes=True)


@router.get("/sync-logs", response_model=SyncLogPage)
async def list_sync_logs(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_active_user)],
    platform: str | None = Query(None),
    status_: str | None = Query(None, alias="status"),
    sku: str | None = Query(None),
    product_id: UUID | None = Query(None),
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> SyncLogPage:
    where = [user_scope(SyncLog, user)]
    if platform:
        where.append(SyncLog.platform == platform)
    if status_:
        where.append(SyncLog.status == status_)
    if product_id:
        where.append(SyncLog.product_id == product_id)
    if since:
        where.append(SyncLog.created_at >= since)
    if until:
        where.append(SyncLog.created_at < until)
    if sku:
        where.append(
            SyncLog.product_id.in_(
                select(Product.id).where(
                    and_(user_scope(Product, user), Product.sku == sku)
                )
            )
        )

    total = (
        await session.execute(select(func.count()).select_from(SyncLog).where(and_(*where)))
    ).scalar_one()
    rows = (
        await session.execute(
            select(SyncLog)
            .where(and_(*where))
            .order_by(SyncLog.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()
    return SyncLogPage(
        items=[SyncLogOut.model_validate(r, from_attributes=True) for r in rows],
        total=int(total),
        limit=limit,
        offset=offset,
    )


@router.get("/sync-logs/stats", response_model=SyncLogStats)
async def sync_logs_stats(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_active_user)],
    window_hours: int = Query(24, ge=1, le=168),
) -> SyncLogStats:
    cutoff = datetime.now(UTC) - timedelta(hours=window_hours)
    rows = (
        await session.execute(
            select(SyncLog.platform, SyncLog.status, func.count())
            .where(
                and_(
                    user_scope(SyncLog, user),
                    SyncLog.created_at >= cutoff,
                )
            )
            .group_by(SyncLog.platform, SyncLog.status)
        )
    ).all()

    totals = {s.value: 0 for s in LinkSyncStatus}
    by_platform: dict[str, dict[str, int]] = {}
    for plat, st, n in rows:
        plat_key = plat.value if plat is not None else "unknown"
        st_key = st.value
        totals[st_key] = totals.get(st_key, 0) + int(n)
        by_platform.setdefault(plat_key, {})[st_key] = int(n)

    return SyncLogStats(
        window_hours=window_hours,
        ok=totals.get("ok", 0),
        skipped=totals.get("skipped", 0),
        retryable=totals.get("retryable", 0),
        fatal=totals.get("fatal", 0),
        requires_review=totals.get("requires_review", 0),
        by_platform=by_platform,
    )


__all__ = ["router", "session_scope"]
