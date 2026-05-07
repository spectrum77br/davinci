"""Webhook Bling tests (Fase 5).

Validates HMAC signature, Redis-backed delivery dedup, payload extraction
and the enqueue → background_jobs handshake. The arq pool is mocked so no
worker connection is needed; Postgres + Redis are real (same fixtures as the
rest of the suite).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    BackgroundJob,
    BackgroundJobType,
    Integration,
    IntegrationPlatform,
    LinkSyncStatus,
    Product,
    ProductLink,
    SyncLog,
    User,
    UserRole,
    UserStatus,
)
from app.redis_client import redis
from app.security.cipher import encrypt_json

WEBHOOK_SECRET = "test-bling-webhook-secret-1234567890"
# Force-overwrite so an empty value loaded from .env via Docker env_file
# doesn't shadow the test secret (setdefault leaves empty strings in place).
os.environ["BLING_WEBHOOK_SECRET"] = WEBHOOK_SECRET
get_settings.cache_clear()  # type: ignore[attr-defined]


def _sign(body: bytes, secret: str = WEBHOOK_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest_asyncio.fixture(autouse=True)
async def _purge_dedup() -> None:
    """Clear webhook dedup keys between tests."""
    keys = [k async for k in redis.scan_iter("webhook:bling:dedupe:*")]
    if keys:
        await redis.delete(*keys)
    yield
    keys = [k async for k in redis.scan_iter("webhook:bling:dedupe:*")]
    if keys:
        await redis.delete(*keys)


@pytest_asyncio.fixture
async def user(db: AsyncSession) -> User:
    u = User(
        open_id=f"email:wh-{uuid.uuid4().hex[:8]}@davinci-test.com",
        email=f"wh-{uuid.uuid4().hex[:8]}@davinci-test.com",
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
        permissions={},
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def _make_product_with_bling_link(
    db: AsyncSession, user: User, *, sku: str, bling_id: int = 4242
) -> tuple[Product, ProductLink]:
    integ = Integration(
        user_id=user.id,
        platform=IntegrationPlatform.BLING,
        name="bling-test",
        credentials=encrypt_json({"access_token": "x", "expires_at": 9999999999}),
    )
    db.add(integ)
    await db.flush()
    p = Product(
        user_id=user.id,
        sku=sku,
        name="webhook product",
        stock=0,
        bling_product_id=bling_id,
    )
    db.add(p)
    await db.flush()
    link = ProductLink(
        user_id=user.id,
        product_id=p.id,
        integration_id=integ.id,
        platform=IntegrationPlatform.BLING,
        external_id=str(bling_id),
        stock=0,
        last_sync_status=LinkSyncStatus.OK,
    )
    db.add(link)
    await db.commit()
    await db.refresh(p)
    await db.refresh(link)
    return p, link


def _stock_event_body(*, sku: str, bling_id: int, stock: int) -> bytes:
    payload: dict[str, Any] = {
        "evento": "produto.estoque.alterado",
        "dados": {
            "id": bling_id,
            "codigo": sku,
            "estoque": {"saldoVirtualTotal": stock},
        },
    }
    return json.dumps(payload).encode()


@pytest.mark.asyncio
async def test_webhook_rejects_invalid_signature(client: AsyncClient) -> None:
    body = _stock_event_body(sku="x", bling_id=1, stock=0)
    r = await client.post(
        "/api/webhooks/bling",
        content=body,
        headers={
            "X-Bling-Signature": "sha256=" + "0" * 64,
            "X-Bling-Event": "produto.estoque.alterado",
            "X-Bling-Delivery": uuid.uuid4().hex,
            "Content-Type": "application/json",
        },
    )
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "bad_signature"


@pytest.mark.asyncio
async def test_webhook_rejects_missing_signature(client: AsyncClient) -> None:
    r = await client.post("/api/webhooks/bling", content=b"{}")
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "missing_signature"


@pytest.mark.asyncio
async def test_webhook_valid_enqueues_sync_product(
    db: AsyncSession, client: AsyncClient, user: User
) -> None:
    sku = f"sku-{uuid.uuid4().hex[:6]}"
    p, link = await _make_product_with_bling_link(db, user, sku=sku, bling_id=7777)
    body = _stock_event_body(sku=sku, bling_id=7777, stock=42)
    delivery_id = uuid.uuid4().hex

    fake_pool = AsyncMock()
    fake_pool.enqueue_job = AsyncMock(return_value=type("J", (), {"job_id": "arq-123"}))
    with patch("app.routers.webhooks.get_arq_pool", return_value=fake_pool):
        r = await client.post(
            "/api/webhooks/bling",
            content=body,
            headers={
                "X-Bling-Signature": _sign(body),
                "X-Bling-Event": "produto.estoque.alterado",
                "X-Bling-Delivery": delivery_id,
                "Content-Type": "application/json",
            },
        )

    assert r.status_code == 200, r.text
    body_out = r.json()
    assert body_out["ack"] is True
    assert body_out["product_id"] == str(p.id)
    assert body_out["links"] == 1
    assert body_out["delivery_id"] == delivery_id

    fake_pool.enqueue_job.assert_awaited_once()
    args = fake_pool.enqueue_job.await_args.args
    assert args[0] == "sync_product_run"
    assert args[2] == str(user.id)
    assert args[3] == str(p.id)
    assert args[4] == [str(link.id)]

    await db.refresh(p)
    assert p.stock == 42

    job = (
        await db.execute(
            select(BackgroundJob).where(BackgroundJob.created_by == user.id)
        )
    ).scalar_one()
    assert job.type == BackgroundJobType.SYNC_PRODUCT
    assert job.payload["trigger"] == "webhook_bling"
    assert job.payload["delivery_id"] == delivery_id
    assert job.arq_job_id == "arq-123"


@pytest.mark.asyncio
async def test_webhook_unknown_sku_logs_and_acks(
    db: AsyncSession, client: AsyncClient, user: User
) -> None:
    # Create an anchor Bling link so the SyncLog row can be attributed to a user.
    await _make_product_with_bling_link(
        db, user, sku=f"anchor-{uuid.uuid4().hex[:6]}", bling_id=1
    )

    body = _stock_event_body(sku="not-here", bling_id=99999, stock=10)
    r = await client.post(
        "/api/webhooks/bling",
        content=body,
        headers={
            "X-Bling-Signature": _sign(body),
            "X-Bling-Event": "produto.estoque.alterado",
            "X-Bling-Delivery": uuid.uuid4().hex,
            "Content-Type": "application/json",
        },
    )
    assert r.status_code == 200
    out = r.json()
    assert out["ack"] is True
    assert out["matched"] is False

    logs = (
        await db.execute(
            select(SyncLog).where(SyncLog.error_code == "webhook_unmatched")
        )
    ).scalars().all()
    assert len(logs) == 1
    assert logs[0].status == LinkSyncStatus.SKIPPED
    assert logs[0].action.value == "webhook_unmatched"


@pytest.mark.asyncio
async def test_webhook_dedupes_duplicate_delivery(
    db: AsyncSession, client: AsyncClient, user: User
) -> None:
    sku = f"sku-{uuid.uuid4().hex[:6]}"
    await _make_product_with_bling_link(db, user, sku=sku, bling_id=1234)
    body = _stock_event_body(sku=sku, bling_id=1234, stock=5)
    delivery_id = uuid.uuid4().hex
    headers = {
        "X-Bling-Signature": _sign(body),
        "X-Bling-Event": "produto.estoque.alterado",
        "X-Bling-Delivery": delivery_id,
        "Content-Type": "application/json",
    }

    fake_pool = AsyncMock()
    fake_pool.enqueue_job = AsyncMock(return_value=type("J", (), {"job_id": "arq-x"}))
    with patch("app.routers.webhooks.get_arq_pool", return_value=fake_pool):
        r1 = await client.post("/api/webhooks/bling", content=body, headers=headers)
        r2 = await client.post("/api/webhooks/bling", content=body, headers=headers)

    assert r1.status_code == 200 and r1.json()["ack"] is True
    assert r2.status_code == 200
    assert r2.json().get("duplicate") is True
    fake_pool.enqueue_job.assert_awaited_once()

    jobs = (
        await db.execute(
            select(BackgroundJob).where(BackgroundJob.created_by == user.id)
        )
    ).scalars().all()
    assert len(jobs) == 1
