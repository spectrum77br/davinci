"""Robô da Logística — fila do "Suspender entrega" (Melhor Envio via executor):
guardas, lease/resultado e os endpoints M2M com X-Agent-Token."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Logistica, LogisticaRoboComando, User, UserRole, UserStatus
from app.services import logistica_robo as robo
from app.services import tuta_devolucoes

TOKEN = "tok-teste-executor"  # noqa: S105 — token de teste, não é segredo
ME = [robo.ACAO_SUSPENDER]  # o que o executor do Mac Santiago declara no lease


@pytest_asyncio.fixture
async def admin(db: AsyncSession) -> User:
    email = f"adm-{uuid.uuid4().hex[:6]}@davinci-test.com"
    u = User(open_id=f"email:{email}", email=email, role=UserRole.ADMIN, status=UserStatus.ACTIVE)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


@pytest.fixture
def agent_token(monkeypatch):
    monkeypatch.setattr(get_settings(), "marketing_agent_token", TOKEN)
    return {"X-Agent-Token": TOKEN}


def _linha(**kw) -> Logistica:
    base = {
        "data": date.today(),
        "pedido_bling": "296762",
        "pedido_marketplace": "701-3967231-6921832",
        "plataforma": "Amazon",
        "conta": "kia",
        "meli_status": {"order_status": "Shipped", "fulfillment_channel": "MFN"},
        "amazon_canal": "proprio",
        "rastreio": "AD912266053BR",
    }
    base.update(kw)
    return Logistica(**base)


def test_pode_suspender_guardas():
    assert robo.pode_suspender(_linha()) is None
    assert robo.pode_suspender(_linha(amazon_canal="dba")) == "logistica_nao_envio_proprio"
    assert robo.pode_suspender(_linha(rastreio="TBA1")) == "logistica_sem_rastreio_correios"
    assert robo.pode_suspender(_linha(entregue_em=datetime.now(UTC))) == "logistica_ja_entregue"
    assert (
        robo.pode_suspender(_linha(suspensao_status="pendente")) == "logistica_suspensao_ja_pedida"
    )
    # Falhou pode pedir de novo.
    assert robo.pode_suspender(_linha(suspensao_status="falhou")) is None


@pytest.mark.asyncio
async def test_fluxo_solicitar_lease_resultado(db: AsyncSession, admin: User):
    row = _linha()
    db.add(row)
    await db.commit()

    cmd = await robo.solicitar_suspensao(db, row, user_id=admin.id)
    assert row.suspensao_status == "pendente" and row.suspensao_em is not None
    assert cmd.payload["rastreio"] == "AD912266053BR" and cmd.payload["commit"] is True
    with pytest.raises(robo.RoboError) as e:
        await robo.solicitar_suspensao(db, row, user_id=admin.id)
    assert e.value.code == "logistica_suspensao_ja_pedida"

    leased = await robo.lease(db, limit=5, acoes=ME)
    assert [c["id"] for c in leased] == [str(cmd.id)]
    assert leased[0]["acao"] == "melhorenvio_suspender" and leased[0]["attempts"] == 1
    # Já reivindicado: não volta.
    assert await robo.lease(db, limit=5, acoes=ME) == []

    await robo.registrar_resultado(db, cmd.id, status="failed", result="modo seco")
    await db.refresh(row)
    assert row.suspensao_status == "falhou" and row.suspensao_detalhe == "modo seco"

    # Falhou → pode pedir de novo; done → solicitada.
    cmd2 = await robo.solicitar_suspensao(db, row, user_id=admin.id)
    await robo.lease(db, limit=5, acoes=ME)
    await robo.registrar_resultado(db, cmd2.id, status="done", result='{"requested":true}')
    await db.refresh(row)
    assert row.suspensao_status == "solicitada"


@pytest.mark.asyncio
async def test_lease_recupera_comando_preso(db: AsyncSession, admin: User):
    row = _linha()
    db.add(row)
    await db.commit()
    cmd = await robo.solicitar_suspensao(db, row, user_id=admin.id)
    # Simula um lease anterior que morreu no meio (executor caiu).
    cmd.status = "claimed"
    cmd.attempts = 1
    cmd.claimed_at = datetime.now(UTC) - timedelta(hours=1)
    await db.commit()
    leased = await robo.lease(db, limit=5, acoes=ME)
    assert [c["id"] for c in leased] == [str(cmd.id)] and leased[0]["attempts"] == 2


@pytest.mark.asyncio
async def test_endpoints_do_agente_exigem_token(client: AsyncClient, agent_token, db: AsyncSession):
    r = await client.post("/api/logistica/agent/lease", json={"limit": 5})
    assert r.status_code == 401
    r = await client.post("/api/logistica/agent/lease", json={"limit": 5}, headers=agent_token)
    assert r.status_code == 200 and r.json() == {"comandos": []}


@pytest.mark.asyncio
async def test_endpoint_suspender_e_resultado(
    client: AsyncClient, agent_token, db: AsyncSession, admin: User, auth_as
):
    row = _linha()
    db.add(row)
    await db.commit()
    auth_as(admin)

    r = await client.post(f"/api/logistica/{row.id}/suspender-entrega")
    assert r.status_code == 200, r.text
    assert r.json()["suspensao_status"] == "pendente"

    r = await client.post(
        "/api/logistica/agent/lease",
        json={"limit": 5, "acoes": ["melhorenvio_suspender"]},
        headers=agent_token,
    )
    comandos = r.json()["comandos"]
    assert len(comandos) == 1 and comandos[0]["payload"]["rastreio"] == "AD912266053BR"

    r = await client.post(
        f"/api/logistica/agent/comandos/{comandos[0]['id']}/resultado",
        json={"status": "done", "result": '{"requested": true}'},
        headers=agent_token,
    )
    assert r.status_code == 200 and r.json()["status"] == "done"
    cmd = (await db.execute(select(LogisticaRoboComando))).scalars().one()
    assert cmd.status == "done"
    await db.refresh(row)
    assert row.suspensao_status == "solicitada"

    # Segundo pedido enquanto já solicitada: 422.
    r = await client.post(f"/api/logistica/{row.id}/suspender-entrega")
    assert r.status_code == 422 and r.json()["detail"]["code"] == "logistica_suspensao_ja_pedida"


@pytest.mark.asyncio
async def test_lease_divide_por_maquina(db: AsyncSession, admin: User):
    """24/09/2026: a suspensão foi pro executor do Mac Santiago. O executor
    antigo (sem `acoes`) não pega mais a suspensão, mas continua com o resto
    da fila (Tuta); o novo pega só o que declarou."""
    row = _linha()
    db.add(row)
    await db.commit()
    suspensao = await robo.solicitar_suspensao(db, row, user_id=admin.id)
    tuta = LogisticaRoboComando(logistica_id=None, acao=tuta_devolucoes.ACAO, payload={})
    db.add(tuta)
    await db.commit()

    antigo = await robo.lease(db, limit=5)
    assert [c["id"] for c in antigo] == [str(tuta.id)]

    santiago = await robo.lease(db, limit=5, acoes=ME)
    assert [c["id"] for c in santiago] == [str(suspensao.id)]
    assert await robo.lease(db, limit=5, acoes=ME) == []
    # Declarar lista vazia = não quero nada.
    assert await robo.lease(db, limit=5, acoes=[]) == []

