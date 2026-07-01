"""Webhook Bling — pedido events (Fase 5b).

Asserts:
* `pedido.alteracao` enqueues `ingest_bling_order_run` on arq with the user's id
* `pedido.exclusao` enqueues with the same task + event flag (worker decides
  to soft-delete instead of fetching)
* webhook with no Bling integration in the system is acked but ignored
* the ingest service flattens `itens[]` into one row per item
* the ingest service marks all rows of an order as `situacao='excluido'`
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
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
    BackgroundJobStatus,
    BackgroundJobType,
    BlingOrder,
    Integration,
    IntegrationPlatform,
    Marketplace,
    Store,
    StoreStatus,
    User,
    UserRole,
    UserStatus,
)
from app.redis_client import redis
from app.security.cipher import encrypt_json
from app.services.bling_orders import (
    mark_order_excluido,
    upsert_order,
)

WEBHOOK_SECRET = "test-bling-webhook-secret-1234567890"
os.environ["BLING_WEBHOOK_SECRET"] = WEBHOOK_SECRET
get_settings.cache_clear()  # type: ignore[attr-defined]


def _sign(body: bytes, secret: str = WEBHOOK_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest_asyncio.fixture(autouse=True)
async def _purge_dedup() -> None:
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
        open_id=f"email:wp-{uuid.uuid4().hex[:8]}@davinci-test.com",
        email=f"wp-{uuid.uuid4().hex[:8]}@davinci-test.com",
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
        permissions={},
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def _attach_bling_integration(db: AsyncSession, user: User) -> Integration:
    integ = Integration(
        user_id=user.id,
        platform=IntegrationPlatform.BLING,
        name="bling-test",
        credentials=encrypt_json({"access_token": "x", "expires_at": 9999999999}),
    )
    db.add(integ)
    await db.commit()
    await db.refresh(integ)
    return integ


def _pedido_event_body(*, bling_order_id: int, evento: str = "pedido.alteracao") -> bytes:
    payload: dict[str, Any] = {
        "evento": evento,
        "dados": {"id": bling_order_id},
    }
    return json.dumps(payload).encode()


# ----------------------------------------------------------------- webhook


@pytest.mark.asyncio
async def test_pedido_alteracao_enqueues_ingest(
    db: AsyncSession, client: AsyncClient, user: User
) -> None:
    await _attach_bling_integration(db, user)
    body = _pedido_event_body(bling_order_id=987654)
    delivery_id = uuid.uuid4().hex

    fake_pool = AsyncMock()
    fake_pool.enqueue_job = AsyncMock(return_value=type("J", (), {"job_id": "arq-p1"}))
    with patch("app.routers.webhooks.get_arq_pool", return_value=fake_pool):
        r = await client.post(
            "/api/webhooks/bling",
            content=body,
            headers={
                "X-Bling-Signature": _sign(body),
                "X-Bling-Event": "pedido.alteracao",
                "X-Bling-Delivery": delivery_id,
                "Content-Type": "application/json",
            },
        )

    assert r.status_code == 200, r.text
    out = r.json()
    assert out["ack"] is True
    assert out["kind"] == "pedido"
    assert out["bling_order_id"] == 987654
    assert out["delivery_id"] == delivery_id

    fake_pool.enqueue_job.assert_awaited_once()
    args = fake_pool.enqueue_job.await_args.args
    assert args[0] == "ingest_bling_order_run"
    assert args[1] == 987654
    assert args[2] == str(user.id)
    assert args[3] == "pedido.alteracao"


@pytest.mark.asyncio
async def test_pedido_exclusao_enqueues_with_event_flag(
    db: AsyncSession, client: AsyncClient, user: User
) -> None:
    await _attach_bling_integration(db, user)
    body = _pedido_event_body(bling_order_id=111, evento="pedido.exclusao")

    fake_pool = AsyncMock()
    fake_pool.enqueue_job = AsyncMock(return_value=type("J", (), {"job_id": "arq-x"}))
    with patch("app.routers.webhooks.get_arq_pool", return_value=fake_pool):
        r = await client.post(
            "/api/webhooks/bling",
            content=body,
            headers={
                "X-Bling-Signature": _sign(body),
                "X-Bling-Event": "pedido.exclusao",
                "X-Bling-Delivery": uuid.uuid4().hex,
                "Content-Type": "application/json",
            },
        )

    assert r.status_code == 200
    args = fake_pool.enqueue_job.await_args.args
    assert args[0] == "ingest_bling_order_run"
    assert args[3] == "pedido.exclusao"


@pytest.mark.asyncio
async def test_pedido_no_integration_acks_and_skips(
    db: AsyncSession, client: AsyncClient
) -> None:
    """No Bling integration anywhere → ack but ignored, no enqueue."""
    body = _pedido_event_body(bling_order_id=555)

    fake_pool = AsyncMock()
    fake_pool.enqueue_job = AsyncMock()
    with patch("app.routers.webhooks.get_arq_pool", return_value=fake_pool):
        r = await client.post(
            "/api/webhooks/bling",
            content=body,
            headers={
                "X-Bling-Signature": _sign(body),
                "X-Bling-Event": "pedido.alteracao",
                "X-Bling-Delivery": uuid.uuid4().hex,
                "Content-Type": "application/json",
            },
        )

    assert r.status_code == 200
    out = r.json()
    assert out["ack"] is True
    assert out["ignored"] == "no_bling_integration"
    fake_pool.enqueue_job.assert_not_awaited()


# ----------------------------------------------------------------- ingest


@pytest.mark.asyncio
async def test_upsert_order_splits_items(db: AsyncSession, user: User) -> None:
    # Stage a Store mapped to Bling loja id 123 so we can assert FK linkage.
    from app.models import Company

    co = Company(razao_social="ACME", apelido="acme")
    db.add(co)
    await db.flush()
    st = Store(
        company_id=co.id,
        marketplace=Marketplace.ML,
        status=StoreStatus.ACTIVE,
        bling_store_id=123,
    )
    db.add(st)
    await db.commit()
    await db.refresh(st)

    raw = {
        "id": 5000,
        "numero": "PED-1",
        "data": "2026-01-15 10:30:00",
        "total": "150.00",
        "totalProdutos": "140.00",
        "valorBase": "140.00",
        "loja": {"id": 123},
        "situacao": {"id": 9},
        "transporte": {"frete": "10.00"},
        "itens": [
            {
                "id": 1,
                "codigo": "SKU-A",
                "descricao": "Item A",
                "quantidade": 2,
                "valor": "50.00",
                "produto": {"id": 800, "categoria": {"id": 5, "descricao": "Cat A"}},
            },
            {
                "id": 2,
                "codigo": "SKU-B",
                "quantidade": 1,
                "valor": "40.00",
                "produto": {"id": 801},
            },
        ],
    }

    n = await upsert_order(db, raw)
    await db.commit()
    assert n == 2

    rows = (
        await db.execute(
            select(BlingOrder).where(BlingOrder.bling_id == 5000).order_by(BlingOrder.item_index)
        )
    ).scalars().all()
    assert len(rows) == 2
    assert rows[0].item_index == 0
    assert rows[0].item_codigo == "SKU-A"
    assert rows[0].item_quantidade == 2
    assert rows[0].store_id == st.id
    assert rows[0].numero == "PED-1"
    assert rows[0].situacao == "9"
    assert rows[0].categoria_nome == "Cat A"
    assert rows[1].item_index == 1
    assert rows[1].item_codigo == "SKU-B"
    assert rows[1].item_produto_id == 801


@pytest.mark.asyncio
async def test_upsert_order_replaces_existing_rows(db: AsyncSession, user: User) -> None:
    raw_v1 = {
        "id": 6000,
        "numero": "PED-2",
        "loja": {"id": 999},
        "itens": [
            {"id": 10, "codigo": "OLD", "quantidade": 1, "valor": "10.00",
             "produto": {"id": 1}},
        ],
    }
    await upsert_order(db, raw_v1)
    await db.commit()

    raw_v2 = {
        "id": 6000,
        "numero": "PED-2",
        "loja": {"id": 999},
        "itens": [
            {"id": 11, "codigo": "NEW-A", "quantidade": 3, "valor": "30.00",
             "produto": {"id": 2}},
            {"id": 12, "codigo": "NEW-B", "quantidade": 1, "valor": "5.00",
             "produto": {"id": 3}},
        ],
    }
    n = await upsert_order(db, raw_v2)
    await db.commit()
    assert n == 2

    rows = (
        await db.execute(
            select(BlingOrder).where(BlingOrder.bling_id == 6000)
            .order_by(BlingOrder.item_index)
        )
    ).scalars().all()
    assert [r.item_codigo for r in rows] == ["NEW-A", "NEW-B"]


@pytest.mark.asyncio
async def test_pedido_webhook_creates_durable_job(
    db: AsyncSession, client: AsyncClient, user: User
) -> None:
    """Cada webhook de pedido grava um BackgroundJob(ingest_bling_order) durável
    e passa o job_id como 5º arg do enqueue — a base do re-drive/alerta."""
    await _attach_bling_integration(db, user)
    body = _pedido_event_body(bling_order_id=424242)

    fake_pool = AsyncMock()
    fake_pool.enqueue_job = AsyncMock(return_value=type("J", (), {"job_id": "arq-dj"}))
    with patch("app.routers.webhooks.get_arq_pool", return_value=fake_pool):
        r = await client.post(
            "/api/webhooks/bling",
            content=body,
            headers={
                "X-Bling-Signature": _sign(body),
                "X-Bling-Event": "pedido.alteracao",
                "X-Bling-Delivery": uuid.uuid4().hex,
                "Content-Type": "application/json",
            },
        )

    assert r.status_code == 200, r.text
    out = r.json()
    job_id = out["job_id"]

    # 5º arg do enqueue = job_id do registro durável
    args = fake_pool.enqueue_job.await_args.args
    assert args[0] == "ingest_bling_order_run"
    assert args[1] == 424242
    assert args[4] == job_id

    db.expire_all()
    job = (
        await db.execute(
            select(BackgroundJob).where(BackgroundJob.id == uuid.UUID(job_id))
        )
    ).scalar_one()
    assert job.type == BackgroundJobType.INGEST_BLING_ORDER
    assert job.status == BackgroundJobStatus.PENDING
    assert job.payload["bling_order_id"] == 424242
    assert job.payload["user_id"] == str(user.id)
    assert job.arq_job_id == "arq-dj"


@pytest.mark.asyncio
async def test_ingest_orders_retry_sweep_reenqueues_failed(
    db: AsyncSession, user: User
) -> None:
    """Um ingest FAILED e maduro é re-enfileirado pelo sweep: status volta a
    PENDING, sweep_attempts incrementa e o enqueue recebe o mesmo job_id."""
    from app.worker import ingest_orders_retry_sweep

    job = BackgroundJob(
        type=BackgroundJobType.INGEST_BLING_ORDER,
        status=BackgroundJobStatus.FAILED,
        created_by=user.id,
        total=1,
        finished_at=datetime.now(UTC) - timedelta(minutes=10),
        error="RuntimeError: bling_order_empty:99001",
        payload={
            "trigger": "webhook_bling",
            "event": "pedido.alteracao",
            "bling_order_id": 99001,
            "user_id": str(user.id),
        },
    )
    db.add(job)
    await db.commit()

    fake_pool = AsyncMock()
    fake_pool.enqueue_job = AsyncMock(return_value=type("J", (), {"job_id": "arq-sw"}))
    with patch("app.worker.get_arq_pool", return_value=fake_pool):
        await ingest_orders_retry_sweep({})

    # Job-specific (robusto a estado cross-test): nosso pedido foi re-enfileirado.
    calls = [c.args for c in fake_pool.enqueue_job.await_args_list]
    ours = [a for a in calls if len(a) >= 5 and a[1] == 99001]
    assert len(ours) == 1, ours
    a = ours[0]
    assert a[0] == "ingest_bling_order_run"
    assert a[2] == str(user.id)
    assert a[3] == "pedido.alteracao"
    assert a[4] == str(job.id)

    db.expire_all()
    refreshed = await db.get(BackgroundJob, job.id)
    assert refreshed.status == BackgroundJobStatus.PENDING
    assert refreshed.error is None
    assert refreshed.payload["sweep_attempts"] == 1
    assert refreshed.arq_job_id == "arq-sw"


@pytest.mark.asyncio
async def test_ingest_orders_retry_sweep_respects_cap(
    db: AsyncSession, user: User
) -> None:
    """Passado o teto de tentativas o sweep NÃO re-enfileira — fica FAILED
    (visível) e o backfill diário é a última rede."""
    from app.worker import INGEST_SWEEP_MAX_ATTEMPTS, ingest_orders_retry_sweep

    job = BackgroundJob(
        type=BackgroundJobType.INGEST_BLING_ORDER,
        status=BackgroundJobStatus.FAILED,
        created_by=user.id,
        total=1,
        finished_at=datetime.now(UTC) - timedelta(minutes=10),
        payload={
            "event": "pedido.alteracao",
            "bling_order_id": 99002,
            "user_id": str(user.id),
            "sweep_attempts": INGEST_SWEEP_MAX_ATTEMPTS,
        },
    )
    db.add(job)
    await db.commit()

    fake_pool = AsyncMock()
    fake_pool.enqueue_job = AsyncMock()
    with patch("app.worker.get_arq_pool", return_value=fake_pool):
        await ingest_orders_retry_sweep({})

    calls = [c.args for c in fake_pool.enqueue_job.await_args_list]
    assert not [a for a in calls if len(a) >= 2 and a[1] == 99002]
    db.expire_all()
    refreshed = await db.get(BackgroundJob, job.id)
    assert refreshed.status == BackgroundJobStatus.FAILED


@pytest.mark.asyncio
async def test_ingest_orders_retry_sweep_skips_fresh_failure(
    db: AsyncSession, user: User
) -> None:
    """Falha recém-ocorrida (dentro da janela de maturação) NÃO é varrida —
    deixa os retries do próprio arq assentarem antes."""
    from app.worker import ingest_orders_retry_sweep

    job = BackgroundJob(
        type=BackgroundJobType.INGEST_BLING_ORDER,
        status=BackgroundJobStatus.FAILED,
        created_by=user.id,
        total=1,
        finished_at=datetime.now(UTC),  # agora — jovem demais
        payload={
            "event": "pedido.alteracao",
            "bling_order_id": 99003,
            "user_id": str(user.id),
        },
    )
    db.add(job)
    await db.commit()

    fake_pool = AsyncMock()
    fake_pool.enqueue_job = AsyncMock()
    with patch("app.worker.get_arq_pool", return_value=fake_pool):
        await ingest_orders_retry_sweep({})

    calls = [c.args for c in fake_pool.enqueue_job.await_args_list]
    assert not [a for a in calls if len(a) >= 2 and a[1] == 99003]


@pytest.mark.asyncio
async def test_mark_order_excluido_stamps_situacao(
    db: AsyncSession, user: User
) -> None:
    raw = {
        "id": 7000,
        "numero": "PED-3",
        "loja": {"id": 1},
        "situacao": {"id": 6},
        "itens": [
            {"id": 1, "codigo": "X", "quantidade": 1, "valor": "10.00",
             "produto": {"id": 1}},
            {"id": 2, "codigo": "Y", "quantidade": 1, "valor": "20.00",
             "produto": {"id": 2}},
        ],
    }
    await upsert_order(db, raw)
    await db.commit()

    n = await mark_order_excluido(db, 7000)
    await db.commit()
    assert n == 2

    rows = (
        await db.execute(
            select(BlingOrder).where(BlingOrder.bling_id == 7000)
        )
    ).scalars().all()
    assert all(r.situacao == "excluido" for r in rows)
