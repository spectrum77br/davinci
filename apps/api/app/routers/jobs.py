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
    JobOut,
    JobPage,
    JobStats,
)
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
        str(user.id),
        body.integration_ids and [str(i) for i in body.integration_ids],
    )
    if arq is not None:
        job.arq_job_id = arq.job_id
    await session.commit()
    return JobCreatedOut(job_id=job.id)
