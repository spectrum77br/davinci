"""Tests for `failed_jobs_alert_scan` cron.

Validates: emits one `sync_failure` alert per recently-failed BackgroundJob,
deduplicates by job id across re-runs, and skips jobs that finished outside
the 10-minute window.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Alert,
    AlertType,
    BackgroundJob,
    BackgroundJobStatus,
    BackgroundJobType,
    User,
    UserRole,
    UserSettings,
    UserStatus,
)
from app.worker import failed_jobs_alert_scan


@pytest_asyncio.fixture
async def user(db: AsyncSession) -> User:
    u = User(
        open_id=f"email:fa-{uuid.uuid4().hex[:8]}@davinci-test.com",
        email=f"fa-{uuid.uuid4().hex[:8]}@davinci-test.com",
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
        permissions={},
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


def _failed_job(
    *, user_id, finished_at: datetime, error: str = "boom", trigger: str = "webhook_bling"
) -> BackgroundJob:
    return BackgroundJob(
        type=BackgroundJobType.SYNC_PRODUCT,
        status=BackgroundJobStatus.FAILED,
        created_by=user_id,
        error=error,
        finished_at=finished_at,
        payload={"trigger": trigger, "delivery_id": "d-1"},
    )


@pytest.mark.asyncio
async def test_recent_failure_emits_alert(db: AsyncSession, user: User) -> None:
    db.add(UserSettings(user_id=user.id))
    job = _failed_job(user_id=user.id, finished_at=datetime.now(UTC) - timedelta(minutes=2))
    db.add(job)
    await db.commit()

    await failed_jobs_alert_scan({})

    alerts = (
        await db.execute(
            select(Alert).where(Alert.user_id == user.id)
        )
    ).scalars().all()
    assert len(alerts) == 1
    a = alerts[0]
    assert a.type == AlertType.SYNC_FAILURE
    assert "sync_product" in a.title
    assert "boom" in a.message
    assert a.payload["job_id"] == str(job.id)
    assert a.dedupe_key == f"sync_failure:job:{job.id}"


@pytest.mark.asyncio
async def test_old_failure_skipped(db: AsyncSession, user: User) -> None:
    db.add(UserSettings(user_id=user.id))
    db.add(_failed_job(
        user_id=user.id,
        finished_at=datetime.now(UTC) - timedelta(minutes=30),
    ))
    await db.commit()

    await failed_jobs_alert_scan({})

    n = (
        await db.execute(
            select(Alert).where(Alert.user_id == user.id)
        )
    ).scalars().all()
    assert n == []


@pytest.mark.asyncio
async def test_dedupe_across_runs(db: AsyncSession, user: User) -> None:
    db.add(UserSettings(user_id=user.id))
    db.add(_failed_job(
        user_id=user.id,
        finished_at=datetime.now(UTC) - timedelta(minutes=1),
    ))
    await db.commit()

    await failed_jobs_alert_scan({})
    await failed_jobs_alert_scan({})  # second run, same window

    alerts = (
        await db.execute(
            select(Alert).where(Alert.user_id == user.id)
        )
    ).scalars().all()
    assert len(alerts) == 1


@pytest.mark.asyncio
async def test_succeeded_job_not_alerted(db: AsyncSession, user: User) -> None:
    db.add(UserSettings(user_id=user.id))
    db.add(BackgroundJob(
        type=BackgroundJobType.SYNC_PRODUCT,
        status=BackgroundJobStatus.SUCCEEDED,
        created_by=user.id,
        finished_at=datetime.now(UTC) - timedelta(minutes=2),
    ))
    await db.commit()

    await failed_jobs_alert_scan({})

    alerts = (
        await db.execute(
            select(Alert).where(Alert.user_id == user.id)
        )
    ).scalars().all()
    assert alerts == []
