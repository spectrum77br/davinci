"""Alerts router tests (Fase 6)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Callable

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Alert,
    AlertSeverity,
    AlertType,
    BackgroundJob,
    BackgroundJobStatus,
    BackgroundJobType,
    User,
    UserRole,
    UserStatus,
)


@pytest_asyncio.fixture
async def user(db: AsyncSession) -> User:
    u = User(
        open_id=f"email:al-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"al-{uuid.uuid4().hex[:6]}@davinci-test.com",
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
        permissions={},
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


@pytest_asyncio.fixture
async def other(db: AsyncSession) -> User:
    u = User(
        open_id=f"email:ot-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"ot-{uuid.uuid4().hex[:6]}@davinci-test.com",
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
        permissions={},
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


def _alert(user_id, *, title="t", read=False, hours_ago=0) -> Alert:
    return Alert(
        user_id=user_id,
        type=AlertType.LOW_STOCK,
        severity=AlertSeverity.WARNING,
        title=title,
        message="m",
        payload={},
        read_at=datetime.now(UTC) if read else None,
        created_at=datetime.now(UTC) - timedelta(hours=hours_ago),
    )


@pytest.mark.asyncio
async def test_list_alerts_paginated_only_user_scope(
    client: AsyncClient,
    auth_as: Callable[[User | None], None],
    db: AsyncSession,
    user: User,
    other: User,
) -> None:
    db.add(_alert(user.id, title="a1"))
    db.add(_alert(user.id, title="a2", read=True, hours_ago=1))
    db.add(_alert(other.id, title="not-mine"))
    await db.commit()

    auth_as(user)
    r = await client.get("/api/alerts")
    assert r.status_code == 200, r.text
    body = r.json()
    titles = [i["title"] for i in body["items"]]
    assert "not-mine" not in titles
    assert set(titles) == {"a1", "a2"}
    assert body["total"] == 2
    assert body["unread"] == 1


@pytest.mark.asyncio
async def test_unread_only_filter(
    client: AsyncClient,
    auth_as: Callable[[User | None], None],
    db: AsyncSession,
    user: User,
) -> None:
    db.add(_alert(user.id, title="u1"))
    db.add(_alert(user.id, title="r1", read=True))
    await db.commit()

    auth_as(user)
    r = await client.get("/api/alerts?unread_only=true")
    assert r.status_code == 200
    titles = [i["title"] for i in r.json()["items"]]
    assert titles == ["u1"]


@pytest.mark.asyncio
async def test_unread_count(
    client: AsyncClient,
    auth_as: Callable[[User | None], None],
    db: AsyncSession,
    user: User,
) -> None:
    db.add(_alert(user.id))
    db.add(_alert(user.id))
    db.add(_alert(user.id, read=True))
    await db.commit()

    auth_as(user)
    r = await client.get("/api/alerts/unread-count")
    assert r.status_code == 200
    assert r.json() == {"unread": 2}


@pytest.mark.asyncio
async def test_mark_read_single(
    client: AsyncClient,
    auth_as: Callable[[User | None], None],
    db: AsyncSession,
    user: User,
) -> None:
    a = _alert(user.id, title="x")
    db.add(a)
    await db.commit()
    await db.refresh(a)

    auth_as(user)
    r = await client.post(f"/api/alerts/{a.id}/read")
    assert r.status_code == 200
    assert r.json()["read_at"] is not None


@pytest.mark.asyncio
async def test_mark_read_404_other_user(
    client: AsyncClient,
    auth_as: Callable[[User | None], None],
    db: AsyncSession,
    user: User,
    other: User,
) -> None:
    a = _alert(other.id)
    db.add(a)
    await db.commit()
    await db.refresh(a)

    auth_as(user)
    r = await client.post(f"/api/alerts/{a.id}/read")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_mark_all_read(
    client: AsyncClient,
    auth_as: Callable[[User | None], None],
    db: AsyncSession,
    user: User,
    other: User,
) -> None:
    db.add(_alert(user.id))
    db.add(_alert(user.id))
    db.add(_alert(user.id, read=True))
    db.add(_alert(other.id))  # untouched
    await db.commit()

    auth_as(user)
    r = await client.post("/api/alerts/read-all")
    assert r.status_code == 200
    assert r.json()["updated"] == 2

    r2 = await client.get("/api/alerts/unread-count")
    assert r2.json() == {"unread": 0}


@pytest.mark.asyncio
async def test_last_daily_sync_returns_latest_sync_all_job(
    client: AsyncClient,
    auth_as: Callable[[User | None], None],
    db: AsyncSession,
    user: User,
) -> None:
    older = BackgroundJob(
        type=BackgroundJobType.SYNC_ALL,
        status=BackgroundJobStatus.SUCCEEDED,
        created_by=user.id,
        total=10,
        processed=10,
        result={"ok": 8, "fatal": 2},
        finished_at=datetime.now(UTC) - timedelta(days=1),
        created_at=datetime.now(UTC) - timedelta(days=1),
    )
    newer = BackgroundJob(
        type=BackgroundJobType.SYNC_ALL,
        status=BackgroundJobStatus.SUCCEEDED,
        created_by=user.id,
        total=20,
        processed=20,
        result={"ok": 19, "fatal": 1},
        finished_at=datetime.now(UTC),
        created_at=datetime.now(UTC),
    )
    db.add_all([older, newer])
    await db.commit()
    await db.refresh(newer)

    auth_as(user)
    r = await client.get("/api/alerts/last-daily-sync")
    assert r.status_code == 200
    body = r.json()
    assert body["job_id"] == str(newer.id)
    assert body["total"] == 20
    assert body["result"] == {"ok": 19, "fatal": 1}


@pytest.mark.asyncio
async def test_last_daily_sync_empty_when_no_job(
    client: AsyncClient,
    auth_as: Callable[[User | None], None],
    user: User,
) -> None:
    auth_as(user)
    r = await client.get("/api/alerts/last-daily-sync")
    assert r.status_code == 200
    assert r.json()["job_id"] is None


@pytest.mark.asyncio
async def test_alerts_require_auth(client: AsyncClient) -> None:
    r = await client.get("/api/alerts")
    assert r.status_code == 401
