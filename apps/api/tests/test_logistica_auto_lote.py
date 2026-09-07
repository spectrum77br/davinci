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

from app.models import Chamado, ChamadoMensagem, Logistica, LogisticaStatus
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
    assert out == {"abertos": 1, "robo": 0, "falhas": 0, "adiados": 0, "pulados": 0}
    assert fake.messages == [(999, "Peço a revisão da decisão", "mediator")]
    await db.refresh(row)
    r = row
    assert r.chamado == "999"
    assert r.chamado_auto_at == t0 and r.chamado_auto_erro is None
    # com o chamado aberto a linha resolve (a regra deixa de ser pendência)
    rules = [(await db.execute(select(LogisticaStatus))).scalar_one()]
    assert logistica_match.estado_resolvido(rules, "Entregue", chamado_aberto=True) is True
    assert logistica_match.estado_resolvido(rules, "Entregue", chamado_aberto=False) is False

    # foi pra aba Chamados: linha de origem logistica, canal api, nº do claim,
    # monitoramento ligado, histórico com o sistema + a abertura enviada
    ch = (await db.execute(select(Chamado).where(Chamado.origem == "logistica"))).scalar_one()
    assert ch.origem_ref == str(row.id) and ch.chamado == "999"
    assert ch.canal == "api" and ch.monitoramento is True
    assert ch.plataforma == "Mercado Livre" and ch.pedido_marketplace == "ML1"
    msgs = (
        await db.execute(
            select(ChamadoMensagem).where(ChamadoMensagem.chamado_id == ch.id)
            .order_by(ChamadoMensagem.created_at)
        )
    ).scalars().all()
    assert [m.tipo for m in msgs] == ["sistema", "abertura"]
    assert msgs[1].status == "enviada" and msgs[1].texto == "Peço a revisão da decisão"

    # 2ª passada: já tem chamado → nada (e o ML não é chamado de novo)
    out2 = await logistica_meli.abrir_chamados_em_lote(db, None, agora=t0 + timedelta(minutes=5))
    assert out2 == {"abertos": 0, "robo": 0, "falhas": 0, "adiados": 0, "pulados": 0}
    assert len(fake.messages) == 1
    assert len((await db.execute(select(Chamado))).scalars().all()) == 1


async def test_abrir_chamado_em_lote_recusa_carimba_e_espera(db, monkeypatch):
    chave = logistica_rules.assinatura_pt(MELI)
    db.add(LogisticaStatus(status_plataforma=chave, abrir_chamado=True, mensagem_chamado="msg"))
    row = _linha(pedido_marketplace="ML2")
    db.add(row)
    await db.commit()
    class _Fora(_FakeML):  # API do ML fora do ar: erro cru, não é "sem reclamação"
        async def get_order(self, order_id):
            raise RuntimeError("ML 503")

        async def get_pack(self, pack_id):
            raise RuntimeError("ML 503")

    fake = _Fora(order={}, claim={})
    _patch_ml(monkeypatch, fake)
    t0 = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)

    out = await logistica_meli.abrir_chamados_em_lote(db, None, agora=t0)
    assert out["falhas"] == 1 and out["abertos"] == 0 and out["robo"] == 0
    await db.refresh(row)
    r = row
    assert r.chamado is None
    assert "ML 503" in (r.chamado_auto_erro or "") and r.chamado_auto_at == t0

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


# ---------------------------------------------------------------- pelo formulário (robô)


async def test_abrir_chamado_sem_reclamacao_vai_pro_robo(db, monkeypatch):
    """Eduardo 07/09: 'se tiver disponível pela venda, faz pela venda; se não,
    pelo formulário'. Sem reclamação do comprador o ML recusa → a Logística
    põe o chamado na aba com canal robô + abertura pendente (com as imagens da
    regra) e NÃO enfileira de novo enquanto o robô não devolve o protocolo."""
    from app.models import ChamadoAnexo, LogisticaStatusAnexo

    chave = logistica_rules.assinatura_pt(MELI)
    regra = LogisticaStatus(status_plataforma=chave, abrir_chamado=True,
                            mensagem_chamado="Poderiam verificar o pedido retido?")
    db.add(regra)
    await db.flush()
    db.add(LogisticaStatusAnexo(status_id=regra.id, filename="prova.png",
                                content_type="image/png", size_bytes=3, blob=b"png"))
    row = _linha(pedido_marketplace="ML5")
    db.add(row)
    await db.commit()
    fake = _FakeML(order={"mediations": []}, claim={})
    _patch_ml(monkeypatch, fake)
    t0 = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)

    out = await logistica_meli.abrir_chamados_em_lote(db, None, agora=t0)
    assert out["robo"] == 1 and out["falhas"] == 0 and out["abertos"] == 0
    await db.refresh(row)
    assert row.chamado is None and row.chamado_auto_erro == "encaminhado_ao_robo"
    ch = (await db.execute(select(Chamado).where(Chamado.origem == "logistica"))).scalar_one()
    assert ch.canal == "robo" and ch.chamado is None and ch.origem_ref == str(row.id)
    msgs = (
        await db.execute(
            select(ChamadoMensagem).where(ChamadoMensagem.chamado_id == ch.id)
            .order_by(ChamadoMensagem.created_at)
        )
    ).scalars().all()
    abertura = [m for m in msgs if m.tipo == "abertura"]
    assert len(abertura) == 1
    assert abertura[0].status == "pendente" and abertura[0].canal == "robo"
    assert abertura[0].texto == "Poderiam verificar o pedido retido?"
    anexos = (
        await db.execute(select(ChamadoAnexo).where(ChamadoAnexo.chamado_id == ch.id))
    ).scalars().all()
    assert [(a.filename, a.mensagem_id) for a in anexos] == [("prova.png", abertura[0].id)]

    # próxima rodada (mesmo depois do prazo de retentativa): robô ainda não
    # devolveu → adia, não duplica a abertura nem bate no ML
    out2 = await logistica_meli.abrir_chamados_em_lote(db, None, agora=t0 + timedelta(hours=7))
    assert out2["adiados"] == 1 and out2["robo"] == 0
    n_abert = (
        await db.execute(
            select(ChamadoMensagem).where(
                ChamadoMensagem.chamado_id == ch.id, ChamadoMensagem.tipo == "abertura"
            )
        )
    ).scalars().all()
    assert len(n_abert) == 1


async def test_lease_por_tipo_e_resultado_devolve_protocolo_pra_logistica(
    db, client, monkeypatch
):
    from app.config import get_settings

    token = "tok-auto-lote"  # noqa: S105
    monkeypatch.setattr(get_settings(), "nf_agent_token", token)
    hdr = {"X-Agent-Token": token}

    chave = logistica_rules.assinatura_pt(MELI)
    db.add(LogisticaStatus(status_plataforma=chave, abrir_chamado=True, mensagem_chamado="msg"))
    row = _linha(pedido_marketplace="ML6")
    db.add(row)
    await db.commit()
    _patch_ml(monkeypatch, _FakeML(order={"mediations": []}, claim={}))
    assert (await logistica_meli.abrir_chamados_em_lote(db, None))["robo"] == 1

    # o robô do Tuta (responder) não vê a tarefa de abrir; o do formulário vê
    r = await client.post("/api/chamados/agent/lease", headers=hdr, json={"tipo": "responder"})
    assert r.status_code == 200 and r.json()["tarefas"] == []
    r = await client.post("/api/chamados/agent/lease", headers=hdr, json={"tipo": "abrir"})
    assert r.status_code == 200, r.text
    tarefas = r.json()["tarefas"]
    assert len(tarefas) == 1 and tarefas[0]["tipo"] == "abrir"
    assert tarefas[0]["texto"] == "msg" and tarefas[0]["pedido_marketplace"] == "ML6"

    # robô abriu no formulário e devolveu o protocolo → aba + Logística
    r = await client.post(
        "/api/chamados/agent/resultado",
        headers=hdr,
        json={"mensagem_id": tarefas[0]["mensagem_id"], "ok": True, "chamado": "480000123",
              "chamado_url": "https://www.mercadolivre.com.br/cases/detail/480000123"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "enviada"
    await db.refresh(row)
    assert row.chamado == "480000123" and row.chamado_auto_erro is None
    ch = (await db.execute(select(Chamado).where(Chamado.origem == "logistica"))).scalar_one()
    assert ch.chamado == "480000123"
    # e a linha resolve na Logística (regra deixa de ser pendência)
    rules = [(await db.execute(select(LogisticaStatus))).scalar_one()]
    assert logistica_match.estado_resolvido(rules, "Entregue", chamado_aberto=True) is True
