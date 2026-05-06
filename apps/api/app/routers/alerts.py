"""Alerts router (Fase 6)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_active_user
from app.models import (
    Alert,
    BackgroundJob,
    BackgroundJobType,
    User,
)
from app.schemas.alerts import (
    AlertListOut,
    AlertOut,
    LastDailySyncOut,
    MarkReadOut,
    UnreadCountOut,
)

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


@router.get("", response_model=AlertListOut)
async def list_alerts(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_active_user)],
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    unread_only: bool = Query(False),
) -> AlertListOut:
    base = select(Alert).where(Alert.user_id == user.id)
    if unread_only:
        base = base.where(Alert.read_at.is_(None))
    stmt = base.order_by(Alert.created_at.desc()).limit(limit).offset(offset)
    rows = (await session.execute(stmt)).scalars().all()

    total = (
        await session.execute(
            select(func.count()).select_from(Alert).where(Alert.user_id == user.id)
        )
    ).scalar_one()
    unread = (
        await session.execute(
            select(func.count())
            .select_from(Alert)
            .where(Alert.user_id == user.id, Alert.read_at.is_(None))
        )
    ).scalar_one()
    return AlertListOut(
        items=[AlertOut.model_validate(r, from_attributes=True) for r in rows],
        total=total,
        unread=unread,
    )


@router.get("/unread-count", response_model=UnreadCountOut)
async def unread_count(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_active_user)],
) -> UnreadCountOut:
    n = (
        await session.execute(
            select(func.count())
            .select_from(Alert)
            .where(Alert.user_id == user.id, Alert.read_at.is_(None))
        )
    ).scalar_one()
    return UnreadCountOut(unread=n)


@router.get("/last-daily-sync", response_model=LastDailySyncOut)
async def last_daily_sync(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_active_user)],
) -> LastDailySyncOut:
    job = (
        await session.execute(
            select(BackgroundJob)
            .where(
                BackgroundJob.created_by == user.id,
                BackgroundJob.type == BackgroundJobType.SYNC_ALL,
            )
            .order_by(BackgroundJob.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if job is None:
        return LastDailySyncOut()
    return LastDailySyncOut(
        job_id=job.id,
        status=job.status.value,
        started_at=job.started_at,
        finished_at=job.finished_at,
        total=job.total,
        processed=job.processed,
        result=job.result or {},
    )


@router.post("/{alert_id}/read", response_model=AlertOut)
async def mark_read(
    alert_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_active_user)],
) -> AlertOut:
    a = (
        await session.execute(
            select(Alert).where(Alert.id == alert_id, Alert.user_id == user.id)
        )
    ).scalar_one_or_none()
    if a is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "alert_not_found"})
    if a.read_at is None:
        a.read_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(a)
    return AlertOut.model_validate(a, from_attributes=True)


@router.post("/read-all", response_model=MarkReadOut)
async def mark_all_read(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_active_user)],
) -> MarkReadOut:
    result = await session.execute(
        update(Alert)
        .where(Alert.user_id == user.id, Alert.read_at.is_(None))
        .values(read_at=datetime.now(UTC))
    )
    await session.commit()
    return MarkReadOut(updated=result.rowcount or 0)
