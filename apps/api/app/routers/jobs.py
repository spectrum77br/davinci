from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_active_user, require_permission
from app.models import (
    BackgroundJob,
    BackgroundJobStatus,
    BackgroundJobType,
    User,
)
from app.schemas.products import (
    AutoLinkIn,
    JobCreatedOut,
    JobDetailsOut,
    JobOut,
    JobPage,
    JobStats,
    JobStatusOut,
)
from app.services.job_details import count_job_details, load_job_details
from app.worker_pool import get_arq_pool

logger = structlog.get_logger()
router = APIRouter(prefix="/api", tags=["jobs"])


def _scope_jobs(user: User):
    """Admins see every user's jobs; non-admins only their own."""
    from sqlalchemy import true
    from app.models.enums import UserRole

    if user.role == UserRole.ADMIN:
        return true()
    return BackgroundJob.created_by == user.id


@router.get("/jobs", response_model=JobPage)
async def list_jobs(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_active_user)],
    type_: str | None = Query(None, alias="type"),
    status_: str | None = Query(None, alias="status"),
    since: datetime | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> JobPage:
    where = [_scope_jobs(user)]
    if type_:
        where.append(BackgroundJob.type == type_)
    if status_:
        where.append(BackgroundJob.status == status_)
    if since:
        where.append(BackgroundJob.created_at >= since)

    total = (
        await session.execute(
            select(func.count()).select_from(BackgroundJob).where(and_(*where))
        )
    ).scalar_one()
    rows = (
        await session.execute(
            select(BackgroundJob)
            .where(and_(*where))
            .order_by(BackgroundJob.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()
    return JobPage(
        items=[JobOut.model_validate(r, from_attributes=True) for r in rows],
        total=int(total),
        limit=limit,
        offset=offset,
    )


@router.get("/jobs/stats", response_model=JobStats)
async def jobs_stats(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_active_user)],
    window_hours: int = Query(24, ge=1, le=168),
) -> JobStats:
    cutoff = datetime.now(UTC) - timedelta(hours=window_hours)
    rows = (
        await session.execute(
            select(BackgroundJob.status, func.count())
            .where(
                and_(
                    _scope_jobs(user),
                    BackgroundJob.created_at >= cutoff,
                )
            )
            .group_by(BackgroundJob.status)
        )
    ).all()
    counts = {st.value if hasattr(st, "value") else str(st): int(n) for st, n in rows}
    return JobStats(
        pending=counts.get("pending", 0),
        running=counts.get("running", 0),
        succeeded=counts.get("succeeded", 0),
        failed=counts.get("failed", 0),
        cancelled=counts.get("cancelled", 0),
    )


@router.get("/jobs/{job_id}", response_model=JobOut)
async def get_job(
    job_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_active_user)],
) -> JobOut:
    j = (
        await session.execute(
            select(BackgroundJob).where(
                and_(BackgroundJob.id == job_id, _scope_jobs(user))
            )
        )
    ).scalar_one_or_none()
    if j is None:
        raise HTTPException(404, detail={"code": "job_not_found"})
    return JobOut.model_validate(j, from_attributes=True)


@router.get("/jobs/{job_id}/status", response_model=JobStatusOut)
async def get_job_status(
    job_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_active_user)],
) -> JobStatusOut:
    """Lightweight poll: everything JobOut has except the `details` array, plus
    `details_count`. The array moved to `background_job_details`; clients fetch
    only the delta via /jobs/{id}/details, so this stays tiny even when the log
    is thousands of entries long."""
    j = (
        await session.execute(
            select(BackgroundJob).where(
                and_(BackgroundJob.id == job_id, _scope_jobs(user))
            )
        )
    ).scalar_one_or_none()
    if j is None:
        raise HTTPException(404, detail={"code": "job_not_found"})
    details_count = await count_job_details(session, job_id)
    return JobStatusOut(
        id=j.id,
        type=j.type.value,
        status=j.status.value,
        total=j.total,
        processed=j.processed,
        payload=j.payload,
        result=j.result,
        error=j.error,
        started_at=j.started_at,
        finished_at=j.finished_at,
        last_heartbeat_at=j.last_heartbeat_at,
        created_at=j.created_at,
        updated_at=j.updated_at,
        details_count=details_count,
    )


@router.get("/jobs/{job_id}/details", response_model=JobDetailsOut)
async def get_job_details(
    job_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_active_user)],
    after: int = Query(0, ge=0),
    limit: int = Query(500, ge=1, le=2000),
) -> JobDetailsOut:
    """Incremental slice of a job's progress log: entries with child-id >
    `after`, oldest first. Returns the raw entry objects plus `max_id` (the
    cursor to pass back as `after` next time)."""
    scoped = (
        await session.execute(
            select(BackgroundJob.id).where(
                and_(BackgroundJob.id == job_id, _scope_jobs(user))
            )
        )
    ).scalar_one_or_none()
    if scoped is None:
        raise HTTPException(404, detail={"code": "job_not_found"})
    items, max_id = await load_job_details(
        session, job_id, after_id=after, limit=limit
    )
    return JobDetailsOut(items=items, max_id=max_id)


@router.post("/jobs/auto-link", response_model=JobCreatedOut, status_code=status.HTTP_201_CREATED)
async def enqueue_auto_link(
    body: AutoLinkIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("produtos", "edit"))],
) -> JobCreatedOut:
    job = BackgroundJob(
        type=BackgroundJobType.AUTO_LINK,
        status=BackgroundJobStatus.PENDING,
        created_by=user.id,
        payload={"integration_ids": [str(i) for i in (body.integration_ids or [])]},
    )
    session.add(job)
    await session.flush()

    pool = await get_arq_pool()
    arq = await pool.enqueue_job(
        "auto_link_run",
        str(job.id),
        body.integration_ids and [str(i) for i in body.integration_ids],
    )
    if arq is not None:
        job.arq_job_id = arq.job_id
    await session.commit()
    return JobCreatedOut(job_id=job.id)


@router.post(
    "/jobs/auto-import-link",
    response_model=JobCreatedOut,
    status_code=status.HTTP_201_CREATED,
)
async def enqueue_auto_import_link(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("produtos", "edit"))],
) -> JobCreatedOut:
    """Scan unlinked listings → attach product_id by SKU and materialize
    `product_links`. Global CRM-mode pass. Mirrors the 02h/14h cron tick."""
    job = BackgroundJob(
        type=BackgroundJobType.AUTO_IMPORT_LINK,
        status=BackgroundJobStatus.PENDING,
        created_by=user.id,
        payload={},
    )
    session.add(job)
    await session.flush()

    pool = await get_arq_pool()
    arq = await pool.enqueue_job("auto_import_link_run", str(job.id))
    if arq is not None:
        job.arq_job_id = arq.job_id
    await session.commit()
    return JobCreatedOut(job_id=job.id)
