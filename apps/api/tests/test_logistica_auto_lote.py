"""Executores AUTOMÁTICOS da aba Status no motor do recarregar (07/09):
`logistica_meli.abrir_chamados_em_lote` (Abrir chamado → mediação do ML com a
Mensagem do chamado) e `logistica_bling.enviar_threema_em_lote` (Mensagem
Threema), ambos com dedupe e sem martelar API."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from sqlalchemy import select

from app.models import Logistica, LogisticaStatus
from app.services import (
    logistica_bling,
    logistica_match,
    logistica_meli,
    logistica_rules,
    threema,
)

pytestmark = pytest.mark.asyncio

MELI = {"order_status": "paid", "ship_status": "delivered"}


class _FakeML:
    def __init__(self, order, claim):
        self._order = order
        self._claim = claim
        self.messages: list = []

    async def get_order(self, order_id):
        return self._order

    async def get_pack(self, pack_id):
        raise RuntimeError("no pack")

    async def get_claim(self, claim_id):
        return self._claim

    async def open_claim_dispute(self, claim_id):
        return {"id": claim_id}

    async def send_claim_message(
        self, claim_id, message, *, receiver_role="mediator", attachments=None
    ):
        self.messages.append((claim_id, message, receiver_role))
        return {"ok": True}


async def _integ(session, conta):
    return object()


def _patch_ml(monkeypatch, fake):
    monkeypatch.setattr(logistica_meli, "_ml_integration_for_conta", _integ)
    monkeypatch.setattr(logistica_meli, "_build_ml_client", lambda session, integ: fake)


def _linha(**kw) -> Logistica:
    base = {
        "plataforma": "Mercado Livre",
        "conta": "loja",
        "pedido_marketplace": "ML1",
        "pedido_bling": "1",
        "meli_status": MELI,
        "status_bling": "Entregue",
    }
    base.update(kw)
    return Logistica(**base)


# ---------------------------------------------------------------- chamado


async def test_abrir_chamado_em_lote_abre_e_carimba(db, monkeypatch):
    chave = logistica_rules.assinatura_pt(MELI)
    db.add(LogisticaStatus(status_plataforma=chave, abrir_chamado=True,
                           mensagem_chamado="Peço a revisão da decisão"))
    row = _linha()
    db.add(row)
    await db.commit()
    fake = _FakeML(
        order={"mediations": [{"id": 999}]},
        claim={"status": "opened",
               "players": [{"role": "respondent",
                            "available_actions": [{"action": "send_message_to_mediator"}]}]},
    )
    _patch_ml(monkeypatch, fake)
    t0 = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)

    out = await logistica_meli.abrir_chamados_em_lote(db, None, agora=t0)
    assert out == {"abertos": 1, "falhas": 0, "adiados": 0, "pulados": 0}
    assert fake.messages == [(999, "Peço a revisão da decisão", "mediator")]
    await db.refresh(row)
    r = row
    assert r.chamado == "999"
    assert r.chamado_auto_at == t0 and r.chamado_auto_erro is None
    # com o chamado aberto a linha resolve (a regra deixa de ser pendência)
    rules = [(await db.execute(select(LogisticaStatus))).scalar_one()]
    assert logistica_match.estado_resolvido(rules, "Entregue", chamado_aberto=True) is True
    assert logistica_match.estado_resolvido(rules, "Entregue", chamado_aberto=False) is False

    # 2ª passada: já tem chamado → nada (e o ML não é chamado de novo)
    out2 = await logistica_meli.abrir_chamados_em_lote(db, None, agora=t0 + timedelta(minutes=5))
    assert out2 == {"abertos": 0, "falhas": 0, "adiados": 0, "pulados": 0}
    assert len(fake.messages) == 1


async def test_abrir_chamado_em_lote_recusa_carimba_e_espera(db, monkeypatch):
    chave = logistica_rules.assinatura_pt(MELI)
    db.add(LogisticaStatus(status_plataforma=chave, abrir_chamado=True, mensagem_chamado="msg"))
    row = _linha(pedido_marketplace="ML2")
    db.add(row)
    await db.commit()
    fake = _FakeML(order={"mediations": []}, claim={})  # comprador ainda não reclamou
    _patch_ml(monkeypatch, fake)
    t0 = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)

    out = await logistica_meli.abrir_chamados_em_lote(db, None, agora=t0)
    assert out["falhas"] == 1 and out["abertos"] == 0
    await db.refresh(row)
    r = row
    assert r.chamado is None
    assert r.chamado_auto_erro == "logistica_sem_reclamacao" and r.chamado_auto_at == t0

    # dentro do prazo de retentativa: adia, não bate na API
    out = await logistica_meli.abrir_chamados_em_lote(db, None, agora=t0 + timedelta(hours=1))
    assert out["adiados"] == 1 and out["falhas"] == 0
    # passado o prazo: tenta de novo
    out = await logistica_meli.abrir_chamados_em_lote(db, None, agora=t0 + timedelta(hours=7))
    assert out["falhas"] == 1


async def test_abrir_chamado_em_lote_respeita_estado_e_ids(db, monkeypatch):
    chave = logistica_rules.assinatura_pt(MELI)
    # regra só vale quando o Bling está "Entregue" — linha em "Problemas" não dispara
    db.add(LogisticaStatus(status_plataforma=chave, status_atual="Entregue",
                           abrir_chamado=True, mensagem_chamado="msg"))
    fora = _linha(pedido_marketplace="ML3", status_bling="Problemas")
    dentro = _linha(pedido_marketplace="ML4", status_bling="Entregue")
    db.add_all([fora, dentro])
    await db.commit()
    fake = _FakeML(
        order={"mediations": [{"id": 5}]},
        claim={"status": "opened",
               "players": [{"role": "respondent",
                            "available_actions": [{"action": "send_message_to_mediator"}]}]},
    )
    _patch_ml(monkeypatch, fake)
    # ids restringe: só a linha "fora" → nada acontece
    out = await logistica_meli.abrir_chamados_em_lote(db, [fora.id])
    assert out["abertos"] == 0 and fake.messages == []
    out = await logistica_meli.abrir_chamados_em_lote(db, [fora.id, dentro.id])
    assert out["abertos"] == 1 and len(fake.messages) == 1


# ---------------------------------------------------------------- threema


async def test_enviar_threema_em_lote_envia_uma_vez(db):
    chave = logistica_rules.assinatura_pt(MELI)
    db.add(LogisticaStatus(status_plataforma=chave, mensagem_threema="avisar equipe",
                           threema_recipients="ABCD1234"))
    row = _linha(pedido_marketplace="ML9", plataforma="Mercado Livre")
    ja = _linha(pedido_marketplace="ML8", threema_enviado_at=datetime(2026, 9, 1, tzinfo=UTC))
    db.add_all([row, ja])
    await db.commit()

    with respx.mock(base_url=threema.THREEMA_API_BASE) as router:
        route = router.post("/send_simple").mock(return_value=httpx.Response(200, text="mid"))
        out = await logistica_bling.enviar_threema_em_lote(db, None)
        assert out == {"enviados": 1, "falhas": 0}
        assert len(route.calls) == 1  # a já avisada não reenvia
        body = route.calls.last.request.content.decode()
        assert "to=ABCD1234" in body and "Pedido+ML9" in body
        # 2ª passada: carimbada → nada
        out2 = await logistica_bling.enviar_threema_em_lote(db, None)
        assert out2 == {"enviados": 0, "falhas": 0}
        assert len(route.calls) == 1
    await db.refresh(row)
    assert row.threema_enviado_at is not None


async def test_enviar_threema_em_lote_gateway_falha_nao_carimba(db):
    chave = logistica_rules.assinatura_pt(MELI)
    db.add(LogisticaStatus(status_plataforma=chave, mensagem_threema="avisar",
                           threema_recipients="ABCD1234"))
    row = _linha(pedido_marketplace="ML7")
    db.add(row)
    await db.commit()
    with respx.mock(base_url=threema.THREEMA_API_BASE) as router:
        router.post("/send_simple").mock(return_value=httpx.Response(500, text="erro"))
        out = await logistica_bling.enviar_threema_em_lote(db, None)
    assert out == {"enviados": 0, "falhas": 1}
    await db.refresh(row)
    assert row.threema_enviado_at is None  # tenta de novo na próxima rodada
