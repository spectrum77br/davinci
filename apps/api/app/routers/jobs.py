from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_active_user, require_permission
from app.models import (
    BackgroundJob,
    BackgroundJobStatus,
    BackgroundJobType,
    User,
)
from app.schemas.products import AutoLinkIn, JobCreatedOut, JobOut
from app.worker_pool import get_arq_pool

logger = structlog.get_logger()
router = APIRouter(prefix="/api", tags=["jobs"])


@router.get("/jobs/{job_id}", response_model=JobOut)
async def get_job(
    job_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_active_user)],
) -> JobOut:
    j = (
        await session.execute(
            select(BackgroundJob).where(
                and_(BackgroundJob.id == job_id, BackgroundJob.created_by == user.id)
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
