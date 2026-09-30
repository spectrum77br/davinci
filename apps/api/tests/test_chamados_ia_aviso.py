# ruff: noqa: E501
"""IA de Chamado parada na tela ("não sou robô" / login) → aviso no Threema.

30/09/2026 (297130), Cairo: "se travar não sou robô me avisa … a mensagem pode
vir para mim cairo". A IA decide `humano` com `parado`; o DaVinci manda o
Threema pra quem está no cadastro `chamados_ia` e registra no histórico."""

from __future__ import annotations

import hashlib

import pytest
from sqlalchemy import select

from app.models import ChamadoCerebro, ChamadoMensagem, ThreemaInformarConfig, UserRole
from app.services import threema

pytestmark = pytest.mark.asyncio

_IA = "cerebro_ia_aviso_teste"  # noqa: S105
_LEGADO = "tok-nf-legado"  # noqa: S105
IA = {"X-Agent-Token": _IA}


def _perms() -> dict:
    return {"chamados": {"view": True, "edit": True, "delete": True}}


@pytest.fixture
def threema_fake(monkeypatch):
    enviados: list[tuple[str, list[str]]] = []

    async def _send_to_all(self, text, recipients=None):
        enviados.append((text, list(recipients or [])))
        return {"sent": list(recipients or []), "failed": []}

    monkeypatch.setattr(threema.ThreemaClient, "send_to_all", _send_to_all)
    return enviados


@pytest.fixture
async def cid(client, make_user, auth_as, db, monkeypatch) -> str:
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "nf_agent_token", _LEGADO)
    auth_as(await make_user(permissions=_perms()))
    db.add(
        ChamadoCerebro(
            nome="IA de Chamado", token_hash=hashlib.sha256(_IA.encode()).hexdigest(), ligada=True
        )
    )
    await db.commit()
    r = await client.post(
        "/api/chamados/agent/registrar",
        headers={"X-Agent-Token": _LEGADO},
        json={
            "pedido_bling": "297130",
            "origem": "logistica",
            "conta": "marquezini",
            "chamado": "480000001",
            "mensagem": "Mediação finalizada com reembolso sem devolução",
            "status_envio": "enviada",
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["chamado_id"]


def _decisao(cid: str, **extra) -> dict:
    return {
        "chamado_id": cid,
        "classe": "ml_formulario_captcha",
        "resumo": "Formulário Fale conosco › E-mail preenchido; ao clicar Continuar abriu o quebra-cabeça.",
        "acao": "humano",
        **extra,
    }


async def _sistema(db) -> list[str]:
    return list(
        (
            await db.execute(
                select(ChamadoMensagem.texto).where(ChamadoMensagem.tipo == "sistema")
            )
        ).scalars()
    )


async def test_parado_em_captcha_avisa_no_threema(client, db, cid, threema_fake):
    db.add(ThreemaInformarConfig(contexto="chamados_ia", recipients="CAIRO123"))
    await db.commit()
    r = await client.post("/api/chamados/agent/analise", headers=IA, json=_decisao(cid, parado="captcha"))
    assert r.status_code == 200, r.text
    assert r.json()["aviso"] == "aviso enviado no Threema pra CAIRO123"
    assert len(threema_fake) == 1
    texto, alvos = threema_fake[0]
    assert alvos == ["CAIRO123"]
    assert 'parou no "não sou robô"' in texto
    assert "Pedido 297130" in texto and "conta marquezini" in texto
    assert "quebra-cabeça" in texto  # o resumo da IA: onde parou
    assert texto.rstrip().endswith("/chamados?search=297130")
    assert any("IA de Chamado parou" in t and "enviado no Threema" in t for t in await _sistema(db))
    # a decisão em si continua normal
    analise = (
        await db.execute(select(ChamadoMensagem.texto).where(ChamadoMensagem.tipo == "analise"))
    ).scalar_one()
    assert analise.endswith("precisa de humano")


async def test_sem_ninguem_cadastrado_decide_e_diz_que_nao_avisou(client, db, cid, threema_fake):
    r = await client.post("/api/chamados/agent/analise", headers=IA, json=_decisao(cid, parado="login"))
    assert r.status_code == 200, r.text
    assert "ninguém cadastrado" in r.json()["aviso"]
    assert threema_fake == []
    assert any("tela de login" in t and "não saiu" in t for t in await _sistema(db))


async def test_threema_sem_config_nao_derruba_a_decisao(client, db, cid, monkeypatch):
    db.add(ThreemaInformarConfig(contexto="chamados_ia", recipients="CAIRO123"))
    await db.commit()

    async def _quebra(self, text, recipients=None):
        raise threema.ThreemaConfigError("threema_gateway_missing")

    monkeypatch.setattr(threema.ThreemaClient, "send_to_all", _quebra)
    r = await client.post("/api/chamados/agent/analise", headers=IA, json=_decisao(cid, parado="captcha"))
    assert r.status_code == 200, r.text
    assert r.json()["aviso"] == "aviso não saiu: o envio no Threema falhou"


async def test_sem_parado_nao_avisa(client, db, cid, threema_fake):
    db.add(ThreemaInformarConfig(contexto="chamados_ia", recipients="CAIRO123"))
    await db.commit()
    r = await client.post("/api/chamados/agent/analise", headers=IA, json=_decisao(cid))
    assert r.status_code == 200, r.text
    assert r.json()["aviso"] is None
    assert threema_fake == []


async def test_parado_so_com_humano(client, cid, threema_fake):
    r = await client.post(
        "/api/chamados/agent/analise", headers=IA, json=_decisao(cid, acao="esperar", parado="captcha")
    )
    assert r.status_code == 422
    r = await client.post("/api/chamados/agent/analise", headers=IA, json=_decisao(cid, parado="outro"))
    assert r.status_code == 422
    assert threema_fake == []


async def test_cadastro_quem_recebe_admin_e_cairo(client, make_user, auth_as):
    auth_as(await make_user(permissions=_perms()))
    assert (await client.get("/api/informar/chamados_ia")).status_code == 403
    auth_as(await make_user(email="sa.geral@tutamail.com", permissions=_perms()))
    assert (await client.get("/api/informar/chamados_ia")).status_code == 200
    auth_as(await make_user(role=UserRole.ADMIN))
    r = await client.get("/api/informar/chamados_ia")
    assert r.status_code == 200 and r.json()["contexto"] == "chamados_ia"
