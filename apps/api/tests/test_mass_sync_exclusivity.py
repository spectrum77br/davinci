"""ITEM 1 — exclusividade "só outro massa": enquanto um Sincronizar Todos ou
Vincular Automático está PENDING/RUNNING, um NOVO massa é recusado com 409. O
sync individual por SKU NÃO passa por esse gate (continua livre).

Também cobre o repoint da fila: o enqueue do auto_link vai pra fila de sync
(`get_arq_sync_pool`), não pra default.
"""
from __future__ import annotations

from collections.abc import Callable
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BackgroundJob,
    BackgroundJobStatus,
    BackgroundJobType,
    User,
    UserRole,
)


async def _running_mass_job(db: AsyncSession, user: User, type_: BackgroundJobType):
    job = BackgroundJob(
        type=type_,
        status=BackgroundJobStatus.RUNNING,
        created_by=user.id,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return job


@pytest.mark.asyncio
async def test_sync_all_recusa_409_quando_ha_massa_ativo(
    client: AsyncClient,
    db: AsyncSession,
    make_user: Callable,
    auth_as: Callable[[User | None], None],
):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    await _running_mass_job(db, admin, BackgroundJobType.AUTO_LINK)

    r = await client.post("/api/jobs/sync-all", json={})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "sync_already_running"


@pytest.mark.asyncio
async def test_auto_link_recusa_409_quando_ha_massa_ativo(
    client: AsyncClient,
    db: AsyncSession,
    make_user: Callable,
    auth_as: Callable[[User | None], None],
):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    await _running_mass_job(db, admin, BackgroundJobType.SYNC_ALL)

    r = await client.post("/api/jobs/auto-link", json={})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "sync_already_running"


@pytest.mark.asyncio
async def test_auto_link_sem_massa_enfileira_na_fila_de_sync(
    client: AsyncClient,
    make_user: Callable,
    auth_as: Callable[[User | None], None],
):
    """Sem massa ativo: o enqueue vai pra fila de sync (get_arq_sync_pool) com o
    job `auto_link_run`, e o endpoint devolve 201."""
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)

    fake_pool = AsyncMock()
    fake_pool.enqueue_job = AsyncMock(return_value=type("J", (), {"job_id": "x"}))
    with patch("app.routers.jobs.get_arq_sync_pool", return_value=fake_pool):
        r = await client.post("/api/jobs/auto-link", json={})

    assert r.status_code == 201
    fake_pool.enqueue_job.assert_awaited_once()
    assert fake_pool.enqueue_job.await_args.args[0] == "auto_link_run"


@pytest.mark.asyncio
async def test_sync_active_endpoint_reflete_massa(
    client: AsyncClient,
    db: AsyncSession,
    make_user: Callable,
    auth_as: Callable[[User | None], None],
):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)

    r = await client.get("/api/sync/active")
    assert r.status_code == 200
    assert r.json()["active"] is False

    await _running_mass_job(db, admin, BackgroundJobType.SYNC_ALL)
    r = await client.get("/api/sync/active")
    assert r.json()["active"] is True
    assert r.json()["type"] == "sync_all"
