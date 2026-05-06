"""Settings webhook-url endpoint tests (Fase 5)."""

from __future__ import annotations

import os
import uuid
from typing import Callable

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.config import get_settings
from app.models import User, UserRole, UserStatus
from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture
async def admin(db: AsyncSession) -> User:
    u = User(
        open_id=f"email:adm-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"adm-{uuid.uuid4().hex[:6]}@davinci-test.com",
        role=UserRole.ADMIN,
        status=UserStatus.ACTIVE,
        permissions={},
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


@pytest_asyncio.fixture
async def viewer(db: AsyncSession) -> User:
    u = User(
        open_id=f"email:viewer-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"viewer-{uuid.uuid4().hex[:6]}@davinci-test.com",
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
        permissions={"sincronizacoes": {"view": True, "edit": False, "delete": False}},
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


@pytest_asyncio.fixture
async def stranger(db: AsyncSession) -> User:
    u = User(
        open_id=f"email:nope-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"nope-{uuid.uuid4().hex[:6]}@davinci-test.com",
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
        permissions={},
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


@pytest.mark.asyncio
async def test_webhook_url_returns_url_and_hint_for_admin(
    client: AsyncClient, auth_as: Callable[[User | None], None], admin: User
) -> None:
    os.environ["BLING_WEBHOOK_SECRET"] = "abcd1234efgh5678"
    get_settings.cache_clear()  # type: ignore[attr-defined]
    auth_as(admin)
    r = await client.get("/api/settings/webhook-url")
    assert r.status_code == 200, r.text
    body = r.json()
    s = get_settings()
    assert body["url"] == f"{s.api_url.rstrip('/')}/api/webhooks/bling"
    assert body["secret_hint"].startswith("abcd")
    assert body["secret_hint"].endswith("5678")
    assert "produto.estoque.alterado" in body["events"]


@pytest.mark.asyncio
async def test_webhook_url_hint_when_secret_short(
    client: AsyncClient, auth_as: Callable[[User | None], None], admin: User
) -> None:
    os.environ["BLING_WEBHOOK_SECRET"] = ""
    get_settings.cache_clear()  # type: ignore[attr-defined]
    auth_as(admin)
    r = await client.get("/api/settings/webhook-url")
    assert r.status_code == 200
    assert r.json()["secret_hint"] == "(não configurado)"


@pytest.mark.asyncio
async def test_webhook_url_allowed_for_viewer_with_permission(
    client: AsyncClient, auth_as: Callable[[User | None], None], viewer: User
) -> None:
    auth_as(viewer)
    r = await client.get("/api/settings/webhook-url")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_webhook_url_forbidden_without_permission(
    client: AsyncClient, auth_as: Callable[[User | None], None], stranger: User
) -> None:
    auth_as(stranger)
    r = await client.get("/api/settings/webhook-url")
    assert r.status_code == 403
