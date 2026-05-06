"""GET/PATCH /api/settings tests (Fase 7)."""

from __future__ import annotations

import uuid
from typing import Callable

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, UserRole, UserSettings, UserStatus


@pytest_asyncio.fixture
async def user(db: AsyncSession) -> User:
    u = User(
        open_id=f"email:u-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"u-{uuid.uuid4().hex[:6]}@davinci-test.com",
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
        permissions={},
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


@pytest.mark.asyncio
async def test_get_settings_returns_defaults_when_missing(
    client: AsyncClient, auth_as: Callable[[User | None], None], user: User
) -> None:
    auth_as(user)
    r = await client.get("/api/settings")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["daily_sync_enabled"] is False
    assert body["daily_sync_time"] is None
    assert body["notify_email"] is True
    assert body["notify_telegram"] is False
    assert body["notify_daily_sync"] is True
    assert body["telegram_chat_id"] is None
    assert body["low_stock_threshold"] is None


@pytest.mark.asyncio
async def test_patch_creates_row_and_updates(
    client: AsyncClient,
    auth_as: Callable[[User | None], None],
    user: User,
    db: AsyncSession,
) -> None:
    auth_as(user)
    r = await client.patch(
        "/api/settings",
        json={
            "daily_sync_enabled": True,
            "daily_sync_time": "08:30:00",
            "low_stock_threshold": 5,
            "notify_telegram": True,
            "telegram_chat_id": "-100111",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["daily_sync_enabled"] is True
    assert body["daily_sync_time"] == "08:30:00"
    assert body["low_stock_threshold"] == 5
    assert body["notify_telegram"] is True
    assert body["telegram_chat_id"] == "-100111"

    us = await db.get(UserSettings, user.id)
    assert us is not None
    assert us.daily_sync_enabled is True
    assert us.low_stock_threshold == 5


@pytest.mark.asyncio
async def test_patch_partial_keeps_other_fields(
    client: AsyncClient, auth_as: Callable[[User | None], None], user: User
) -> None:
    auth_as(user)
    await client.patch("/api/settings", json={"notify_email": False})
    r = await client.patch("/api/settings", json={"notify_telegram": True})
    assert r.status_code == 200
    body = r.json()
    assert body["notify_email"] is False
    assert body["notify_telegram"] is True


@pytest.mark.asyncio
async def test_patch_validates_sync_interval_bounds(
    client: AsyncClient, auth_as: Callable[[User | None], None], user: User
) -> None:
    auth_as(user)
    r = await client.patch("/api/settings", json={"sync_interval_minutes": 1})
    assert r.status_code == 422
    r = await client.patch("/api/settings", json={"sync_interval_minutes": 99999})
    assert r.status_code == 422
    r = await client.patch("/api/settings", json={"sync_interval_minutes": 60})
    assert r.status_code == 200
    assert r.json()["sync_interval_minutes"] == 60


@pytest.mark.asyncio
async def test_patch_requires_auth(client: AsyncClient) -> None:
    r = await client.patch("/api/settings", json={"notify_email": False})
    assert r.status_code in (401, 403)
