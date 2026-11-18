"""Suspender a entrega direto na API do Melhor Envio (18/11/2026).

O robô de tela falhava no 309193 ("o menu de ações abriu, mas Suspender entrega não
apareceu"). A tela do ME suspende por `POST /api/v2/me/orders/{envio}/suspend` com
{reason, reason_description} — lido no dash.js; os motivos (value/name) lidos na
janela do site em 18/11.
"""

from __future__ import annotations

import json
import uuid
from datetime import date

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Logistica, LogisticaRoboComando, User, UserRole, UserStatus
from app.services import melhor_envio as me

RASTREIO = "AE157152522BR"
ENVIO_ID = "a33d156e-6d19-49ad-9706-e47a2685fd24"


@pytest_asyncio.fixture
async def admin(db: AsyncSession) -> User:
    email = f"adm-{uuid.uuid4().hex[:6]}@davinci-test.com"
    u = User(open_id=f"email:{email}", email=email, role=UserRole.ADMIN, status=UserStatus.ACTIVE)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


def _linha(**kw) -> Logistica:
    base = {
        "data": date(2026, 11, 16),
        "pedido_bling": "309193",
        "pedido_marketplace": "701-2078084-0220268",
        "plataforma": "Amazon",
        "conta": "kia",
        "amazon_canal": "proprio",
        "rastreio": RASTREIO,
    }
    base.update(kw)
    return Logistica(**base)


def _fake_me(monkeypatch, *, token="tok", http=200, resposta=None, envios=None):  # noqa: S107 — token de teste
    pedidos: list[dict] = []

    async def _token(session):
        return token, {}

    async def _envios(tok, *, page=1, q=None, extra=None):
        dados = (
            envios
            if envios is not None
            else [{"id": ENVIO_ID, "tracking": RASTREIO, "status": "posted", "tickets": []}]
        )
        return 200, {"data": [e for e in dados if e.get("tracking") == q]}

    async def _suspender(tok, envio_id, *, motivo, descricao=""):
        pedidos.append({"envio": envio_id, "motivo": motivo})
        corpo = (
            resposta
            if resposta is not None
            else {
                "order": {"id": envio_id, "status": "posted"},
                "suspended_at": "2026-11-18 11:30:00",
            }
        )
        return http, corpo

    monkeypatch.setattr(me, "token_do_painel", _token)
    monkeypatch.setattr(me, "ler_envios", _envios)
    monkeypatch.setattr(me, "suspender_envio", _suspender)
    return pedidos


async def test_suspende_pela_api_e_volta_solicitada(
    client: AsyncClient, db, auth_as, admin, monkeypatch
):
    pedidos = _fake_me(monkeypatch)
    row = _linha(suspensao_status="falhou")  # a tentativa do robô que falhou
    db.add(row)
    await db.commit()
    auth_as(admin)

    r = await client.post(f"/api/logistica/{row.id}/suspender-entrega")
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["suspensao_status"] == "solicitada"
    detalhe = json.loads(corpo["suspensao_detalhe"])
    assert detalhe["via"] == "api" and detalhe["ok"] is True
    assert "Meu cliente desistiu da compra" in detalhe["reason"]
    assert pedidos == [{"envio": ENVIO_ID, "motivo": "customer_withdrew_purchase"}]
    # nada na fila do robô
    assert (await db.execute(select(LogisticaRoboComando))).scalars().all() == []

    # já solicitada: não pede de novo
    r = await client.post(f"/api/logistica/{row.id}/suspender-entrega")
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "logistica_suspensao_ja_pedida"
    assert len(pedidos) == 1


async def test_recusa_do_me_fica_falhou_com_a_mensagem(
    client: AsyncClient, db, auth_as, admin, monkeypatch
):
    _fake_me(monkeypatch, http=422, resposta={"message": "Envio já entregue."})
    row = _linha()
    db.add(row)
    await db.commit()
    auth_as(admin)
    r = await client.post(f"/api/logistica/{row.id}/suspender-entrega")
    assert r.status_code == 200
    assert r.json()["suspensao_status"] == "falhou"
    assert "Envio já entregue." in json.loads(r.json()["suspensao_detalhe"])["reason"]


async def test_envio_fora_do_me_fica_falhou(client: AsyncClient, db, auth_as, admin, monkeypatch):
    pedidos = _fake_me(monkeypatch, envios=[])
    row = _linha()
    db.add(row)
    await db.commit()
    auth_as(admin)
    r = await client.post(f"/api/logistica/{row.id}/suspender-entrega")
    assert r.status_code == 200
    assert r.json()["suspensao_status"] == "falhou"
    assert "não aparece nos envios" in json.loads(r.json()["suspensao_detalhe"])["reason"]
    assert pedidos == []


async def test_sem_token_cai_no_robo_como_antes(
    client: AsyncClient, db, auth_as, admin, monkeypatch
):
    pedidos = _fake_me(monkeypatch, token=None)
    row = _linha()
    db.add(row)
    await db.commit()
    auth_as(admin)
    r = await client.post(f"/api/logistica/{row.id}/suspender-entrega")
    assert r.status_code == 200
    assert r.json()["suspensao_status"] == "pendente"
    assert pedidos == []
    [cmd] = (await db.execute(select(LogisticaRoboComando))).scalars().all()
    assert cmd.payload["rastreio"] == RASTREIO


async def test_dba_e_entregue_nao_suspendem(client: AsyncClient, db, auth_as, admin, monkeypatch):
    pedidos = _fake_me(monkeypatch)
    dba = _linha(amazon_canal="dba", pedido_bling="309530")
    db.add(dba)
    await db.commit()
    auth_as(admin)
    r = await client.post(f"/api/logistica/{dba.id}/suspender-entrega")
    assert r.status_code == 422 and r.json()["detail"]["code"] == "logistica_nao_envio_proprio"
    assert pedidos == []


async def test_suspender_envio_valida_motivo_e_id():
    http, corpo = await me.suspender_envio("tok", "../x", motivo="customer_withdrew_purchase")
    assert http == 400
    http, corpo = await me.suspender_envio("tok", ENVIO_ID, motivo="inventado")
    assert http == 400
