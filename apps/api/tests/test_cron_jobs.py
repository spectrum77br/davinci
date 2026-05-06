"""Cron job tests (Fase 5).

Drive each cron function directly with `ctx={}` so we sidestep arq's runtime
and verify their persistent side effects against the real test DB.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, time, timedelta
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BackgroundJob,
    BackgroundJobStatus,
    BackgroundJobType,
    User,
    UserRole,
    UserSettings,
    UserStatus,
)
from app.worker import (
    _next_month_partition_bounds,
    background_jobs_gc,
    daily_sync_scheduler,
    sync_logs_partition_gc,
)


@pytest_asyncio.fixture
async def user(db: AsyncSession) -> User:
    u = User(
        open_id=f"email:cron-{uuid.uuid4().hex[:8]}@davinci-test.com",
        email=f"cron-{uuid.uuid4().hex[:8]}@davinci-test.com",
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
        permissions={},
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


@pytest.mark.asyncio
async def test_next_month_partition_bounds_rolls_year() -> None:
    name, start, end = _next_month_partition_bounds(datetime(2026, 12, 15, tzinfo=UTC))
    assert name == "sync_logs_y2027m01"
    assert start == "2027-01-01"
    assert end == "2027-02-01"


@pytest.mark.asyncio
async def test_sync_logs_partition_gc_creates_next_month_idempotent(
    db: AsyncSession,
) -> None:
    """conftest builds `sync_logs` as a plain table via Base.metadata.create_all
    (so Postgres-only declarative partitioning isn't reproduced). We rebuild it
    as a partitioned table for the duration of this test, then restore the
    plain version so the rest of the suite is unaffected."""
    schema = "davinci_test"
    name, _, _ = _next_month_partition_bounds(datetime.now(UTC))

    # Snapshot original sync_logs definition, swap to a minimal partitioned one.
    await db.execute(text(f'DROP TABLE IF EXISTS "{schema}".sync_logs CASCADE'))
    await db.execute(
        text(
            f'CREATE TABLE "{schema}".sync_logs ('
            f"  id UUID NOT NULL DEFAULT gen_random_uuid(),"
            f"  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),"
            f"  PRIMARY KEY (id, created_at)"
            f") PARTITION BY RANGE (created_at)"
        )
    )
    # Default partition so other code paths inserting into sync_logs (post-test
    # cleanup) don't hit "no partition" errors.
    await db.execute(
        text(
            f'CREATE TABLE "{schema}".sync_logs_default '
            f'PARTITION OF "{schema}".sync_logs DEFAULT'
        )
    )
    await db.commit()

    try:
        await sync_logs_partition_gc({})
        await sync_logs_partition_gc({})  # idempotent

        rows = await db.execute(
            text(
                "SELECT 1 FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = :schema AND c.relname = :name"
            ),
            {"schema": schema, "name": name},
        )
        assert rows.first() is not None
    finally:
        # Restore the plain (non-partitioned) sync_logs the rest of the suite
        # was built against. Re-run create_all just for sync_logs.
        from app.models import Base

        await db.execute(text(f'DROP TABLE IF EXISTS "{schema}".sync_logs CASCADE'))
        await db.commit()
        from app.db import engine as _async_engine

        sync_logs_table = Base.metadata.tables[f"{schema}.sync_logs"]
        async with _async_engine.begin() as conn:
            await conn.run_sync(
                lambda sync_conn: sync_logs_table.create(sync_conn, checkfirst=True)
            )


@pytest.mark.asyncio
async def test_background_jobs_gc_marks_orphans_failed(
    db: AsyncSession, user: User
) -> None:
    stale = BackgroundJob(
        type=BackgroundJobType.SYNC_ALL,
        status=BackgroundJobStatus.RUNNING,
        created_by=user.id,
        last_heartbeat_at=datetime.now(UTC) - timedelta(minutes=30),
    )
    fresh = BackgroundJob(
        type=BackgroundJobType.SYNC_ALL,
        status=BackgroundJobStatus.RUNNING,
        created_by=user.id,
        last_heartbeat_at=datetime.now(UTC) - timedelta(seconds=10),
    )
    no_hb = BackgroundJob(
        type=BackgroundJobType.SYNC_ALL,
        status=BackgroundJobStatus.RUNNING,
        created_by=user.id,
        last_heartbeat_at=None,
    )
    db.add_all([stale, fresh, no_hb])
    await db.commit()

    await background_jobs_gc({})

    await db.refresh(stale)
    await db.refresh(fresh)
    await db.refresh(no_hb)

    assert stale.status == BackgroundJobStatus.FAILED
    assert stale.error == "orphan_no_heartbeat"
    assert stale.finished_at is not None
    assert no_hb.status == BackgroundJobStatus.FAILED
    assert fresh.status == BackgroundJobStatus.RUNNING


@pytest.mark.asyncio
async def test_daily_sync_scheduler_skips_already_run_today(
    db: AsyncSession, user: User
) -> None:
    """User with daily_sync_time inside the current 5-min window must not get
    a second sync_all job if one was already created today."""
    from app.worker import SP_TZ

    now_sp = datetime.now(SP_TZ).replace(second=0, microsecond=0)
    db.add(
        UserSettings(
            user_id=user.id,
            daily_sync_enabled=True,
            daily_sync_time=now_sp.time(),
        )
    )
    db.add(
        BackgroundJob(
            type=BackgroundJobType.SYNC_ALL,
            status=BackgroundJobStatus.SUCCEEDED,
            created_by=user.id,
            payload={"trigger": "daily_sync"},
        )
    )
    await db.commit()

    fake_pool = AsyncMock()
    fake_pool.enqueue_job = AsyncMock(return_value=type("J", (), {"job_id": "x"}))
    with patch("app.worker.get_arq_pool", return_value=fake_pool):
        await daily_sync_scheduler({})

    fake_pool.enqueue_job.assert_not_awaited()
    rows = (
        await db.execute(
            select(BackgroundJob).where(BackgroundJob.created_by == user.id)
        )
    ).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_daily_sync_scheduler_enqueues_when_no_run_today(
    db: AsyncSession, user: User
) -> None:
    from app.worker import SP_TZ

    now_sp = datetime.now(SP_TZ).replace(second=0, microsecond=0)
    db.add(
        UserSettings(
            user_id=user.id,
            daily_sync_enabled=True,
            daily_sync_time=now_sp.time(),
        )
    )
    await db.commit()

    fake_pool = AsyncMock()
    fake_pool.enqueue_job = AsyncMock(
        return_value=type("J", (), {"job_id": "arq-daily"})
    )
    with patch("app.worker.get_arq_pool", return_value=fake_pool):
        await daily_sync_scheduler({})

    fake_pool.enqueue_job.assert_awaited_once()
    args = fake_pool.enqueue_job.await_args.args
    assert args[0] == "sync_all_run"
    assert args[2] == str(user.id)

    job = (
        await db.execute(
            select(BackgroundJob).where(BackgroundJob.created_by == user.id)
        )
    ).scalar_one()
    assert job.type == BackgroundJobType.SYNC_ALL
    assert job.payload["trigger"] == "daily_sync"
    assert job.arq_job_id == "arq-daily"


@pytest.mark.asyncio
async def test_daily_sync_scheduler_ignores_disabled(
    db: AsyncSession, user: User
) -> None:
    from app.worker import SP_TZ

    now_sp = datetime.now(SP_TZ).replace(second=0, microsecond=0)
    db.add(
        UserSettings(
            user_id=user.id,
            daily_sync_enabled=False,
            daily_sync_time=now_sp.time(),
        )
    )
    await db.commit()

    fake_pool = AsyncMock()
    fake_pool.enqueue_job = AsyncMock()
    with patch("app.worker.get_arq_pool", return_value=fake_pool):
        await daily_sync_scheduler({})
    fake_pool.enqueue_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_daily_sync_scheduler_ignores_outside_window(
    db: AsyncSession, user: User
) -> None:
    """A daily_sync_time at least 1h away from `now` falls outside the 5-min
    enqueue window and must be ignored."""
    from app.worker import SP_TZ

    now_sp = datetime.now(SP_TZ).replace(second=0, microsecond=0)
    far_minutes = 60
    far = (now_sp - timedelta(minutes=far_minutes)).time()
    if (
        time(now_sp.hour, now_sp.minute) <= far
        <= time(now_sp.hour, now_sp.minute)
    ):
        # Pathological boundary — pick something even further in the past.
        far = (now_sp - timedelta(minutes=180)).time()

    db.add(
        UserSettings(
            user_id=user.id,
            daily_sync_enabled=True,
            daily_sync_time=far,
        )
    )
    await db.commit()

    fake_pool = AsyncMock()
    fake_pool.enqueue_job = AsyncMock()
    with patch("app.worker.get_arq_pool", return_value=fake_pool):
        await daily_sync_scheduler({})
    fake_pool.enqueue_job.assert_not_awaited()
