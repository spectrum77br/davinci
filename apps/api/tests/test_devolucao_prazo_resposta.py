"""Prazo de resposta da loja nas devoluções (Vinicius 16/09: "colocar no painel
o prazo de resposta pra não perder mais prazo").

Cobre a corrente inteira com dados FALSOS (sem rede):
  · TikTok `seller_next_action_response` → ReturnInfo.acao_pendente/prazo_acao
    (menor prazo; entrada suja nunca levanta);
  · sync do retorno grava/limpa `devolucao_rastreio.acao_auto/prazo_acao_auto`;
  · `devolucao_acao_avisos`: janela de 24 h, um aviso por (caso, prazo), sem
    destinatário não manda nem carimba, Threema caído não carimba;
  · router: coluna "Prazo p/ responder" + ação em PT na aba Acompanhamento.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DevolucaoRastreio, Logistica, ThreemaInformarConfig
from app.routers.devolutions import _prazo_resposta
from app.services import devolucao_acao_avisos as avisos
from app.services import devolucao_rastreio_sync as sync
from app.services import logistica_meli, logistica_rules, logistica_shopee, logistica_tiktok, logistica_track
from app.services.devolucao_returns import ReturnInfo

AGORA = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)  # 09:00 BRT
PRAZO_18 = datetime(2026, 9, 18, 13, 19, tzinfo=UTC)  # 18/09 10:19 BRT — caso real 293798


# ---- parser ------------------------------------------------------------------


def test_acao_pendente_pega_o_menor_prazo_e_ignora_lixo():
    d = {
        "seller_next_action_response": [
            {"action": "SELLER_RESPOND_RECEIVE_PACKAGE", "deadline": 1789600000},
            {"action": "SELLER_RESPOND_REFUND", "deadline": 1789500000},
            {"action": "X", "deadline": "abc"},
            "lixo",
            {"deadline": None},
        ]
    }
    acao, prazo = logistica_tiktok._acao_pendente(d)
    assert acao == "SELLER_RESPOND_REFUND"
    assert prazo == datetime.fromtimestamp(1789500000, tz=UTC)
    # Sem ação pendente (AWAITING_BUYER_SHIP, caso fechado…) → nada.
    assert logistica_tiktok._acao_pendente({"seller_next_action_response": []}) == (None, None)
    assert logistica_tiktok._acao_pendente({}) == (None, None)
    assert logistica_tiktok._acao_pendente({"seller_next_action_response": "x"}) == (None, None)


def test_return_info_carrega_acao_e_prazo():
    info = logistica_tiktok._tiktok_return_info(
        {
            "order_id": "585845785461163452",
            "return_id": "R1",
            "return_status": "RETURN_OR_REFUND_REQUEST_PENDING",
            "return_type": "REFUND",
            "seller_next_action_response": [
                {"action": "SELLER_RESPOND_REFUND", "deadline": int(PRAZO_18.timestamp())}
            ],
        }
    )
    assert info.acao_pendente == "SELLER_RESPOND_REFUND"
    assert info.prazo_acao == PRAZO_18
    assert info.return_type == "REFUND"


def test_prazo_do_pedido_e_o_menor_entre_os_casos_vivos():
    """Pedido com devolução cancelada + reembolso pendente (294865 na época):
    o caso escolhido pro status é o vivo; o prazo também tem que ser o do vivo
    — e havendo dois vivos, o mais curto. Caso encerrado não empresta prazo."""
    cancelada = {
        "order_id": "1", "return_id": "A", "return_status": "RETURN_OR_REFUND_REQUEST_CANCEL",
        "return_type": "RETURN_AND_REFUND", "update_time": 100,
        "seller_next_action_response": [{"action": "SELLER_RESPOND_RETURN", "deadline": 1000}],
    }
    reembolso = {
        "order_id": "1", "return_id": "B", "return_status": "RETURN_OR_REFUND_REQUEST_PENDING",
        "return_type": "REFUND", "update_time": 200,
        "seller_next_action_response": [{"action": "SELLER_RESPOND_REFUND", "deadline": 5000}],
    }
    devolucao = {
        "order_id": "1", "return_id": "C", "return_status": "BUYER_SHIPPED_ITEM",
        "return_type": "RETURN_AND_REFUND", "update_time": 150,
        "seller_next_action_response": [{"action": "SELLER_RESPOND_RECEIVE_PACKAGE", "deadline": 3000}],
    }
    assert logistica_tiktok._acao_pendente_do_pedido([cancelada]) == (None, None)
    assert logistica_tiktok._acao_pendente_do_pedido([cancelada, reembolso]) == (
        "SELLER_RESPOND_REFUND", datetime.fromtimestamp(5000, tz=UTC)
    )
    assert logistica_tiktok._acao_pendente_do_pedido([cancelada, reembolso, devolucao]) == (
        "SELLER_RESPOND_RECEIVE_PACKAGE", datetime.fromtimestamp(3000, tz=UTC)
    )


@pytest.mark.asyncio
async def test_returns_por_pedido_usa_o_menor_prazo_do_pedido(db: AsyncSession, monkeypatch):
    class _Fake:
        async def get_return_list(self, *, order_ids=None, **kw):
            return [
                {"order_id": "585", "return_id": "B", "return_status": "RETURN_OR_REFUND_REQUEST_PENDING",
                 "return_type": "REFUND", "update_time": 200,
                 "seller_next_action_response": [{"action": "SELLER_RESPOND_REFUND", "deadline": 5000}]},
                {"order_id": "585", "return_id": "C", "return_status": "BUYER_SHIPPED_ITEM",
                 "return_type": "RETURN_AND_REFUND", "update_time": 150, "return_tracking_number": "AP1BR",
                 "seller_next_action_response": [{"action": "SELLER_RESPOND_RECEIVE_PACKAGE", "deadline": 3000}]},
            ]

    async def _integ(session, conta):
        return conta

    monkeypatch.setattr(logistica_tiktok, "_tiktok_integration_for_conta", _integ)
    monkeypatch.setattr(logistica_tiktok, "_build_tiktok_client", lambda s, i, *, lock=None: _Fake())
    linha = Logistica(plataforma="TikTok", conta="mini", pedido_bling="294865", pedido_marketplace="585")
    out = await logistica_tiktok.returns_por_pedido(db, [linha])
    info = out["294865"]
    # Status/rastreio: o vivo mais recente (o reembolso); prazo: o mais curto (a devolução).
    assert info.status == "RETURN_OR_REFUND_REQUEST_PENDING" and info.return_type == "REFUND"
    assert info.acao_pendente == "SELLER_RESPOND_RECEIVE_PACKAGE"
    assert info.prazo_acao == datetime.fromtimestamp(3000, tz=UTC)


def test_acao_em_pt():
    assert logistica_rules.acao_plataforma_pt("tiktok", "SELLER_RESPOND_RECEIVE_PACKAGE") == (
        "Confirmar ou recusar o pacote recebido no TikTok"
    )
    assert logistica_rules.acao_plataforma_pt("TikTok", "SELLER_RESPOND_REFUND") == (
        "Responder ao pedido de reembolso no TikTok"
    )
    # Ação nova sem tradução aparece crua — nunca esconde um prazo.
    assert logistica_rules.acao_plataforma_pt("tiktok", "SELLER_DO_X") == "Ação pendente: SELLER_DO_X"
    assert logistica_rules.acao_plataforma_pt("tiktok", None) is None
    assert _prazo_resposta("tiktok", "SELLER_RESPOND_REFUND", PRAZO_18) == (
        PRAZO_18, "Responder ao pedido de reembolso no TikTok"
    )
    # Ação sem prazo não vira coluna.
    assert _prazo_resposta("tiktok", "SELLER_RESPOND_REFUND", None) == (None, None)


# ---- sync grava e limpa ------------------------------------------------------


class _FakeThreema:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.enviados: list[tuple[str, list[str]]] = []

    async def send_to_all(self, texto, recipients):
        if self.fail:
            raise RuntimeError("threema caiu")
        self.enviados.append((texto, list(recipients)))
        return {"sent": list(recipients), "failed": []}


@pytest.fixture
def fakes(monkeypatch):
    respostas: dict[str, dict[str, ReturnInfo]] = {"tiktok": {}, "shopee": {}, "ml": {}}

    def _mk(key):
        async def _fn(session, linhas):
            return respostas[key]
        return _fn

    monkeypatch.setattr(logistica_tiktok, "returns_por_pedido", _mk("tiktok"), raising=False)
    monkeypatch.setattr(logistica_shopee, "returns_por_pedido", _mk("shopee"), raising=False)
    monkeypatch.setattr(logistica_meli, "returns_por_pedido", _mk("ml"), raising=False)

    async def _register(numbers):
        return {"ok": True}

    monkeypatch.setattr(logistica_track, "register", _register)

    async def _sem_correios(session):
        return {"consultados": 0, "entregues": 0, "localizacoes": 0}

    monkeypatch.setattr(sync, "_puxar_correios", _sem_correios)
    # O sync chama os avisos com o Threema real: aqui um falso que nunca manda.
    monkeypatch.setattr(avisos.threema, "ThreemaClient", lambda: _FakeThreema())
    return respostas


async def _seed(db: AsyncSession, pedido: str, **kw) -> Logistica:
    row = Logistica(
        pedido_bling=pedido, plataforma="TikTok", pedido_marketplace=f"mk-{pedido}", conta="mini",
        cliente_nome="Fulano de Tal", **kw,
    )
    db.add(row)
    await db.commit()
    return row


async def _rastreio(db: AsyncSession, pedido: str) -> DevolucaoRastreio:
    return (
        await db.execute(select(DevolucaoRastreio).where(DevolucaoRastreio.pedido_bling == pedido))
    ).scalar_one()


@pytest.mark.asyncio
async def test_sync_grava_o_prazo_e_limpa_quando_a_acao_some(db: AsyncSession, fakes):
    pedido = f"5{uuid4().hex[:6]}"
    await _seed(db, pedido)
    fakes["tiktok"][pedido] = ReturnInfo(
        fonte="tiktok", status="BUYER_SHIPPED_ITEM", tracking="JT0001BR", carrier="J&T",
        created_at=AGORA, updated_at=AGORA, return_id="R1", return_type="RETURN_AND_REFUND",
        acao_pendente="SELLER_RESPOND_RECEIVE_PACKAGE", prazo_acao=PRAZO_18,
    )
    await sync.run(db, pedidos=[pedido])
    r = await _rastreio(db, pedido)
    assert r.acao_auto == "SELLER_RESPOND_RECEIVE_PACKAGE"
    assert r.prazo_acao_auto == PRAZO_18

    # Loja respondeu: a plataforma para de pedir ação → prazo some da tela.
    fakes["tiktok"][pedido] = fakes["tiktok"][pedido]._replace(acao_pendente=None, prazo_acao=None)
    await sync.run(db, pedidos=[pedido])
    await db.refresh(r)
    assert r.acao_auto is None and r.prazo_acao_auto is None


# ---- avisos ------------------------------------------------------------------


def _row(pedido: str, prazo: datetime | None, **kw) -> DevolucaoRastreio:
    base = dict(
        pedido_bling=pedido, fonte_auto="tiktok", devolucao_status_auto="BUYER_SHIPPED_ITEM",
        devolucao_tipo_auto="RETURN_AND_REFUND", acao_auto="SELLER_RESPOND_RECEIVE_PACKAGE",
        prazo_acao_auto=prazo, rastreio_auto="AP1BR", transportadora_auto="Correios",
    )
    base.update(kw)
    return DevolucaoRastreio(**base)


def test_aviso_devido_janela_e_dedupe():
    # 25 h antes: ainda não; 23 h antes: sim; vencido há 2 h: sim (ainda vale
    # correr); vencido há 3 dias: não (a plataforma já decidiu).
    assert not avisos.aviso_devido(_row("1", AGORA + timedelta(hours=25)), AGORA)
    assert avisos.aviso_devido(_row("1", AGORA + timedelta(hours=23)), AGORA)
    assert avisos.aviso_devido(_row("1", AGORA - timedelta(hours=2)), AGORA)
    assert not avisos.aviso_devido(_row("1", AGORA - timedelta(days=3)), AGORA)
    assert not avisos.aviso_devido(_row("1", None), AGORA)
    # Só reembolso PENDENTE esperando SELLER_RESPOND_REFUND tem robô próprio
    # (chamados_tiktok_reembolso) → aqui não avisa. Qualquer outra combinação
    # num caso só-reembolso ninguém mais avisa → avisa aqui.
    so_reembolso = dict(
        devolucao_tipo_auto="REFUND", acao_auto="SELLER_RESPOND_REFUND",
        devolucao_status_auto="RETURN_OR_REFUND_REQUEST_PENDING",
    )
    assert not avisos.aviso_devido(_row("1", AGORA + timedelta(hours=5), **so_reembolso), AGORA)
    assert avisos.aviso_devido(
        _row("1", AGORA + timedelta(hours=5), **{**so_reembolso, "acao_auto": "SELLER_RESPOND_X"}), AGORA
    )
    assert avisos.aviso_devido(
        _row("1", AGORA + timedelta(hours=5), **{**so_reembolso, "devolucao_status_auto": "AWAITING_BUYER_RESPONSE"}), AGORA
    )
    # Já avisado PRA ESSE prazo → não repete; prazo novo no mesmo caso → avisa.
    prazo = AGORA + timedelta(hours=20)
    assert not avisos.aviso_devido(
        _row("1", prazo, aviso_prazo_acao_para=prazo, aviso_prazo_acao_at=AGORA - timedelta(hours=1)), AGORA
    )
    assert avisos.aviso_devido(
        _row("1", prazo, aviso_prazo_acao_para=prazo - timedelta(days=2), aviso_prazo_acao_at=AGORA - timedelta(days=2)), AGORA
    )


def test_segundo_aviso_quando_entra_nas_4h():
    """Avisou 24 h antes; quando faltam < 4 h sai o ÚLTIMO AVISO — e só ele."""
    prazo = AGORA + timedelta(hours=3)
    avisado_cedo = _row("1", prazo, aviso_prazo_acao_para=prazo, aviso_prazo_acao_at=AGORA - timedelta(hours=20))
    assert avisos.aviso_devido(avisado_cedo, AGORA)
    # Faltando 5 h ainda não (só entra na faixa com 4 h).
    assert not avisos.aviso_devido(avisado_cedo, AGORA - timedelta(hours=2))
    # O segundo já saiu (carimbo dentro da faixa urgente) → silêncio.
    avisado_urgente = _row("1", prazo, aviso_prazo_acao_para=prazo, aviso_prazo_acao_at=AGORA - timedelta(minutes=30))
    assert not avisos.aviso_devido(avisado_urgente, AGORA)
    # Primeiro aviso já dentro da faixa urgente (prazo apareceu tarde) → um só.
    assert not avisos.aviso_devido(avisado_urgente, AGORA + timedelta(hours=1))
    txt = avisos.mensagem_aviso(_row("1", prazo), None, AGORA)
    assert txt.startswith("🚨 DaVinci — Devolução TikTok: faltam 3 h pra responder — ÚLTIMO AVISO")


def test_falta_em_texto_de_gente():
    assert avisos.falta(AGORA + timedelta(hours=9, minutes=30), AGORA) == "faltam 9 h"
    assert avisos.falta(AGORA + timedelta(minutes=20), AGORA) == "vence em menos de 1 h"
    assert avisos.falta(AGORA + timedelta(days=3), AGORA) == "faltam 3 dias"
    assert avisos.falta(AGORA - timedelta(hours=3), AGORA) == "vencido há 3 h"
    # Arredonda pra baixo nos dois sentidos (2 h 50 min vencido = "2 h").
    assert avisos.falta(AGORA - timedelta(hours=2, minutes=50), AGORA) == "vencido há 2 h"
    assert avisos.falta(AGORA - timedelta(minutes=20), AGORA) == "vencido há menos de 1 h"
    assert avisos.falta(AGORA - timedelta(days=3), AGORA) == "vencido há 3 dias"
    assert avisos.falta(None, AGORA) == ""


def test_mensagem_diz_o_que_fazer_e_ate_quando():
    linha = Logistica(
        pedido_bling="292357", plataforma="TikTok", pedido_marketplace="5859", conta="jlas",
        cliente_nome="Maria",
    )
    prazo = AGORA + timedelta(hours=9)
    txt = avisos.mensagem_aviso(_row("292357", prazo), linha, AGORA)
    assert txt.splitlines()[0] == "⚠️ DaVinci — Devolução TikTok: faltam 9 h pra responder na plataforma"
    assert "Pedido 292357 (jlas) · TikTok 5859" in txt
    assert "Cliente: Maria" in txt
    assert "Pacote de volta: AP1BR (Correios)" in txt
    assert "Situação: Cliente enviou o item de volta" in txt
    assert "O que fazer: Confirmar ou recusar o pacote recebido no TikTok" in txt
    assert "Prazo: 17/09 18:00 (faltam 9 h)" in txt
    assert "decide sozinha" in txt
    # Vencido: cabeçalho de alarme.
    txt2 = avisos.mensagem_aviso(_row("1", AGORA - timedelta(hours=2)), None, AGORA)
    assert txt2.startswith("🚨 DaVinci — Devolução TikTok: prazo de resposta VENCIDO (vencido há 2 h)")
    assert "Pedido 1 (?) · TikTok ?" in txt2


async def _cadastra_destinatarios(db: AsyncSession, ids: str) -> None:
    db.add(ThreemaInformarConfig(contexto=avisos.CONTEXTO_AUTO, recipients=ids))
    await db.commit()


@pytest.mark.asyncio
async def test_run_manda_uma_vez_por_prazo_e_so_pra_quem_esta_em_devolucao(db: AsyncSession):
    p1, p2, p3 = (f"6{uuid4().hex[:6]}" for _ in range(3))
    await _seed(db, p1)
    await _seed(db, p2)
    db.add(_row(p1, AGORA + timedelta(hours=9)))     # devido
    db.add(_row(p2, AGORA + timedelta(days=3)))      # longe ainda
    db.add(_row(p3, AGORA + timedelta(hours=1)))     # devido, mas saiu de Aguardando Devolução
    await db.commit()
    await _cadastra_destinatarios(db, "ABCDEFGH, IJKLMNOP")
    cli = _FakeThreema()

    out = await avisos.run(db, pedidos=[p1, p2], agora=AGORA, client=cli)

    assert out == {"casos": 2, "devidos": 1, "enviados": 1, "falhas": 0, "sem_destinatarios": 0}
    assert len(cli.enviados) == 1
    texto, dest = cli.enviados[0]
    assert dest == ["ABCDEFGH", "IJKLMNOP"]
    assert f"Pedido {p1} (mini)" in texto and "Cliente: Fulano de Tal" in texto
    r1 = await _rastreio(db, p1)
    assert r1.aviso_prazo_acao_at == AGORA and r1.aviso_prazo_acao_para == AGORA + timedelta(hours=9)

    # Rodada seguinte: mesmo prazo → silêncio.
    out = await avisos.run(db, pedidos=[p1, p2], agora=AGORA + timedelta(minutes=30), client=cli)
    assert out["enviados"] == 0 and len(cli.enviados) == 1

    # A plataforma abriu outra ação com prazo novo → avisa de novo.
    r1.prazo_acao_auto = AGORA + timedelta(hours=20)
    await db.commit()
    out = await avisos.run(db, pedidos=[p1, p2], agora=AGORA + timedelta(hours=1), client=cli)
    assert out["enviados"] == 1 and len(cli.enviados) == 2


@pytest.mark.asyncio
async def test_run_sem_destinatario_nao_manda_nem_carimba(db: AsyncSession):
    p1 = f"7{uuid4().hex[:6]}"
    await _seed(db, p1)
    db.add(_row(p1, AGORA + timedelta(hours=2)))
    await db.commit()
    cli = _FakeThreema()

    out = await avisos.run(db, pedidos=[p1], agora=AGORA, client=cli)

    assert out["devidos"] == 1 and out["enviados"] == 0 and out["sem_destinatarios"] == 1
    assert cli.enviados == []
    assert (await _rastreio(db, p1)).aviso_prazo_acao_at is None


@pytest.mark.asyncio
async def test_run_threema_caido_nao_carimba(db: AsyncSession):
    p1 = f"8{uuid4().hex[:6]}"
    await _seed(db, p1)
    db.add(_row(p1, AGORA + timedelta(hours=2)))
    await db.commit()
    await _cadastra_destinatarios(db, "ABCDEFGH")

    out = await avisos.run(db, pedidos=[p1], agora=AGORA, client=_FakeThreema(fail=True))

    assert out["falhas"] == 1 and out["enviados"] == 0
    assert (await _rastreio(db, p1)).aviso_prazo_acao_at is None


# ---- Shopee -------------------------------------------------------------------


def _det_shopee(**kw) -> dict:
    """Detalhe da devolução no formato real (medido em prod 16/09, pedido 292317:
    entregue 16/09 09:54 BRT → return_seller_due_date 19/09 09:50)."""
    base = {
        "status": "PROCESSING", "return_solution": 0, "needs_logistics": True,
        "due_date": 1788890340, "return_seller_due_date": 1789825800, "return_ship_due_date": 1789823640,
        "reverse_logistics_status": "LOGISTICS_DELIVERY_DONE", "update_time": 1789566866, "create_time": 1788721140,
        "seller_proof": {"seller_proof_status": "", "seller_evidence_deadline": None},
        "seller_compensation": {"seller_compensation_status": "", "seller_compensation_due_date": None},
        "negotiation": {"negotiation_status": "", "latest_solution": "RETURN_REFUND", "offer_due_date": None},
    }
    base.update(kw)
    return base


def test_shopee_prazo_de_conferir_so_com_o_pacote_entregue():
    f = logistica_shopee._acao_pendente_shopee
    assert f(_det_shopee(), "PROCESSING") == (
        "SHOPEE_CONFERIR_PACOTE", datetime.fromtimestamp(1789825800, tz=UTC)
    )
    # A Shopee manda a data ANTES da entrega (estimativa, ex. 289941): não vale.
    assert f(_det_shopee(reverse_logistics_status="LOGISTICS_PICKUP_DONE"), "PROCESSING") == (None, None)
    # Já contestou / já pagou: não há mais o que conferir.
    assert f(_det_shopee(), "SELLER_DISPUTE") == (None, None)
    assert f(_det_shopee(), "REFUND_PAID") == (None, None)
    # Sem a data, sem prazo.
    assert f(_det_shopee(return_seller_due_date=None), "PROCESSING") == (None, None)


def test_shopee_outros_prazos_da_loja_e_o_mais_curto_vence():
    f = logistica_shopee._acao_pendente_shopee
    # Pedido recém-aberto esperando a loja responder.
    assert f(_det_shopee(status="REQUESTED", reverse_logistics_status="LOGISTICS_NOT_STARTED"), "REQUESTED") == (
        "SHOPEE_RESPONDER_SOLICITACAO", datetime.fromtimestamp(1788890340, tz=UTC)
    )
    # Em análise (JUDGING) a vez é da Shopee: sem prazo da loja…
    assert f(_det_shopee(status="JUDGING", reverse_logistics_status="LOGISTICS_NOT_STARTED"), "JUDGING") == (None, None)
    # …a não ser que ela peça prova.
    assert f(
        _det_shopee(status="JUDGING", reverse_logistics_status="LOGISTICS_NOT_STARTED",
                    seller_proof={"seller_proof_status": "PENDING", "seller_evidence_deadline": 1789000000}),
        "JUDGING",
    ) == ("SHOPEE_ENVIAR_EVIDENCIAS", datetime.fromtimestamp(1789000000, tz=UTC))
    # Proposta do cliente pendente com prazo.
    assert f(
        _det_shopee(negotiation={"negotiation_status": "PENDING_RESPOND", "offer_due_date": 1789700000}), "PROCESSING"
    ) == ("SHOPEE_RESPONDER_PROPOSTA", datetime.fromtimestamp(1789700000, tz=UTC))
    # Proposta pendente SEM data (caso real 289370) → cai no próximo prazo válido.
    assert f(
        _det_shopee(negotiation={"negotiation_status": "PENDING_RESPOND", "offer_due_date": None}), "PROCESSING"
    ) == ("SHOPEE_CONFERIR_PACOTE", datetime.fromtimestamp(1789825800, tz=UTC))
    # Entrada suja não levanta.
    assert f(None, None) == (None, None)
    assert f({"seller_proof": "x", "negotiation": 3}, "PROCESSING") == (None, None)
    assert logistica_rules.acao_plataforma_pt("shopee", "SHOPEE_CONFERIR_PACOTE") == (
        "Conferir o pacote recebido e responder na Shopee"
    )


class _FakeShopeeComDetalhe:
    """get_return_list (janela por create_time) + get_return_detail."""

    def __init__(self, caso: dict, det: dict):
        self.caso, self.det, self.detalhes = caso, det, 0

    async def get_return_list(self, *, create_time_from=None, create_time_to=None, **kw):
        return [self.caso] if create_time_from <= int(self.caso["create_time"]) <= create_time_to else []

    async def get_return_detail(self, return_sn):
        self.detalhes += 1
        return self.det


def _patch_shopee(monkeypatch, fake) -> None:
    from types import SimpleNamespace

    async def _integ(session, conta):
        return SimpleNamespace(name=conta)

    monkeypatch.setattr(logistica_shopee, "_shopee_integration_for_conta", _integ)
    monkeypatch.setattr(logistica_shopee, "_build_shopee_client", lambda s, i, *, lock=None: fake)


@pytest.mark.asyncio
async def test_shopee_returns_por_pedido_traz_o_prazo_e_respeita_os_dois_conjuntos(db: AsyncSession, monkeypatch):
    """`ja_entregues` sozinho poupa o detalhe (como sempre); `reconsultar`
    força o detalhe mesmo entregue (prazo de conferir); erro no detalhe deixa
    o prazo como DESCONHECIDO (o sync mantém o anterior) em vez de apagar."""
    import time
    from datetime import date

    agora = int(time.time())
    caso = {"order_sn": "SN1", "return_sn": "R1", "status": "PROCESSING", "create_time": agora - 10 * 86400,
            "update_time": agora - 3600, "tracking_number": "BR123"}
    det = _det_shopee(update_time=agora - 3600, return_seller_due_date=agora + 3 * 86400)
    fake = _FakeShopeeComDetalhe(caso, det)
    _patch_shopee(monkeypatch, fake)
    linha = Logistica(plataforma="Shopee", conta="atv", pedido_bling="292317", pedido_marketplace="SN1",
                      data=date.today() - timedelta(days=12))

    out = await logistica_shopee.returns_por_pedido(db, [linha])
    info = out["292317"]
    assert info.acao_pendente == "SHOPEE_CONFERIR_PACOTE"
    assert info.prazo_acao == datetime.fromtimestamp(agora + 3 * 86400, tz=UTC)
    assert info.entregue_em is not None and fake.detalhes == 1 and not info.prazo_desconhecido

    # Entregue há tempo e fora do `reconsultar`: sem detalhe, sem prazo (janela fechou).
    out = await logistica_shopee.returns_por_pedido(db, [linha], ja_entregues={"292317"})
    assert out["292317"].prazo_acao is None and fake.detalhes == 1

    # Entregue há pouco (em `reconsultar`): consulta o detalhe mesmo assim.
    out = await logistica_shopee.returns_por_pedido(db, [linha], ja_entregues={"292317"}, reconsultar={"292317"})
    assert out["292317"].acao_pendente == "SHOPEE_CONFERIR_PACOTE" and fake.detalhes == 2

    # Detalhe caiu: prazo desconhecido (não apaga), status/rastreio da lista seguem.
    async def _boom(return_sn):
        raise RuntimeError("shopee 429")

    monkeypatch.setattr(fake, "get_return_detail", _boom)
    out = await logistica_shopee.returns_por_pedido(db, [linha])
    assert out["292317"].prazo_desconhecido is True and out["292317"].prazo_acao is None
    assert out["292317"].status == "PROCESSING" and out["292317"].tracking == "BR123"


@pytest.mark.asyncio
async def test_sync_mantem_o_prazo_quando_o_detalhe_nao_respondeu(db: AsyncSession, fakes):
    pedido = f"4{uuid4().hex[:6]}"
    await _seed(db, pedido)
    fakes["tiktok"][pedido] = ReturnInfo(
        fonte="tiktok", status="BUYER_SHIPPED_ITEM", tracking=None, carrier=None,
        created_at=AGORA, updated_at=AGORA, return_id="R1",
        acao_pendente="SELLER_RESPOND_RECEIVE_PACKAGE", prazo_acao=PRAZO_18,
    )
    await sync.run(db, pedidos=[pedido])
    fakes["tiktok"][pedido] = fakes["tiktok"][pedido]._replace(
        acao_pendente=None, prazo_acao=None, prazo_desconhecido=True
    )
    await sync.run(db, pedidos=[pedido])
    r = await _rastreio(db, pedido)
    assert r.prazo_acao_auto == PRAZO_18 and r.acao_auto == "SELLER_RESPOND_RECEIVE_PACKAGE"


@pytest.mark.asyncio
async def test_sync_reconsulta_o_detalhe_nos_primeiros_dias_apos_a_entrega(db: AsyncSession, fakes, monkeypatch):
    """`ja_entregues` só leva quem chegou há mais de DIAS_DETALHE_APOS_ENTREGA
    dias — o prazo de conferir (3 dias) mora no detalhe."""
    recente, antigo = (f"9{uuid4().hex[:6]}" for _ in range(2))
    for p in (recente, antigo):
        db.add(Logistica(pedido_bling=p, plataforma="Shopee", pedido_marketplace=f"SN-{p}", conta="atv"))
    agora = datetime.now(UTC)
    db.add(DevolucaoRastreio(pedido_bling=recente, pacote_entregue_em=agora - timedelta(days=2)))
    db.add(DevolucaoRastreio(pedido_bling=antigo, pacote_entregue_em=agora - timedelta(days=10)))
    await db.commit()
    em_analise = f"9{uuid4().hex[:6]}"
    db.add(Logistica(pedido_bling=em_analise, plataforma="Shopee", pedido_marketplace=f"SN-{em_analise}", conta="atv"))
    db.add(DevolucaoRastreio(pedido_bling=em_analise, pacote_entregue_em=agora - timedelta(days=20),
                             devolucao_status_auto="JUDGING"))
    await db.commit()
    visto: dict = {}

    async def _shopee(session, linhas, ja_entregues=None, reconsultar=None):
        visto["ja_entregues"] = set(ja_entregues or ())
        visto["reconsultar"] = set(reconsultar or ())
        return {}

    monkeypatch.setattr(logistica_shopee, "returns_por_pedido", _shopee)
    await sync.run(db, pedidos=[recente, antigo, em_analise])
    # Todos os entregues continuam em `ja_entregues` (decisões da SPX não mudam)…
    assert {recente, antigo, em_analise} <= visto["ja_entregues"]
    # …mas só quem chegou há pouco ou ainda está em análise volta ao detalhe.
    assert visto["reconsultar"] == {recente, em_analise}


# ---- Mercado Livre -------------------------------------------------------------


def _claim_ml(acoes: list[dict]) -> dict:
    return {
        "id": 5570660970, "type": "mediations", "stage": "dispute", "status": "closed",
        "players": [
            {"role": "complainant", "type": "buyer", "available_actions": []},
            {"role": "respondent", "type": "seller", "available_actions": acoes},
            {"role": "mediator", "type": "internal", "available_actions": [{"action": "x", "due_date": "2026-09-30T00:00:00.000-04:00"}]},
        ],
    }


def test_ml_revisao_calculada_so_com_o_pacote_entregue_e_a_acao_liberada():
    f = logistica_meli._acao_pendente_ml
    entregue = datetime(2026, 9, 15, 10, 40, tzinfo=UTC)
    # Caso real 292172 (16/09): claim encerrado, pacote entregue, revisão liberada.
    liberada = _claim_ml([{"action": "return_review_fail", "mandatory": False, "due_date": None},
                          {"action": "return_review_ok", "mandatory": False, "due_date": None}])
    assert f(liberada, entregue) == ("ML_REVISAR_DEVOLUCAO", entregue + timedelta(days=3))
    # Sem entrega ao vendedor ainda → sem prazo (a janela não abriu).
    assert f(liberada, None) == (None, None)
    # Entregue, mas o ML já fechou a janela (ação sumiu) → sem prazo.
    assert f(_claim_ml([]), entregue) == (None, None)
    # Ação do mediador com due_date NÃO é da loja.
    assert f(_claim_ml([]), None) == (None, None)


def test_ml_acao_com_due_date_do_proprio_ml_vence_a_calculada():
    f = logistica_meli._acao_pendente_ml
    entregue = datetime(2026, 9, 15, 10, 40, tzinfo=UTC)
    claim = _claim_ml([
        {"action": "send_message_to_mediator", "mandatory": True, "due_date": "2026-09-16T08:00:00.000-04:00"},
        {"action": "return_review_fail", "mandatory": False, "due_date": None},
    ])
    acao, prazo = f(claim, entregue)
    assert acao == "ML_SEND_MESSAGE_TO_MEDIATOR"
    assert prazo == datetime(2026, 9, 16, 12, 0, tzinfo=UTC)
    assert logistica_rules.acao_plataforma_pt("ml", acao) == "Responder ao mediador do Mercado Livre"
    assert logistica_rules.acao_plataforma_pt("Mercado Livre", "ML_REVISAR_DEVOLUCAO").startswith(
        "Revisar a devolução recebida no Mercado Livre"
    )
    # Entrada suja não levanta.
    assert f(None, entregue) == (None, None)
    assert f({"players": "x"}, entregue) == (None, None)


@pytest.mark.asyncio
async def test_ml_return_info_traz_o_prazo_e_marca_desconhecido_se_o_claim_falhar(monkeypatch):
    """Caminho completo com client falso: pedido → claim → returns (perna do
    vendedor entregue) → shipment → claim (ações) → prazo calculado."""
    # Entregue ONTEM (janela de revisão aberta) — datas relativas a hoje.
    entregue = (datetime.now(UTC) - timedelta(days=1)).replace(microsecond=0)
    iso = entregue.strftime("%Y-%m-%dT%H:%M:%S.000+00:00")
    ret = {
        "id": 159477768, "status": "delivered", "status_money": "refunded", "refund_at": "delivered",
        "date_created": "2026-09-10T10:00:00.000-04:00", "last_updated": iso,
        "shipments": [{"id": 901, "status": "delivered", "destination": {"name": "seller_address"}}],
    }
    claim = _claim_ml([{"action": "return_review_fail", "mandatory": False, "due_date": None}])

    class _Fake:
        falhar_claim = False

        async def get_order(self, oid):
            return {"id": oid, "mediations": [{"id": 5570660970}]}

        async def get_claim_returns(self, cid):
            return [ret]

        async def get_shipment(self, sid):
            return {"id": sid, "status": "delivered", "tracking_number": "ML123", "tracking_method": "Mercado Envios",
                    "status_history": {"date_delivered": iso}, "last_updated": iso}

        async def get_claim(self, cid):
            if self.falhar_claim:
                raise RuntimeError("ml 500")
            return claim

    fake = _Fake()
    info = await logistica_meli._return_info_for_pedido(fake, "2000012345")
    assert info.entregue_em == entregue
    assert info.acao_pendente == "ML_REVISAR_DEVOLUCAO"
    assert info.prazo_acao == entregue + timedelta(days=3)
    assert not info.prazo_desconhecido

    fake.falhar_claim = True
    info = await logistica_meli._return_info_for_pedido(fake, "2000012345")
    assert info.prazo_desconhecido is True and info.prazo_acao is None
    assert info.entregue_em == entregue  # o resto segue normal

    # Entregue há muito tempo (janela fechada): não gasta chamada no claim.
    fake.falhar_claim = False
    velho = (datetime.now(UTC) - timedelta(days=20)).strftime("%Y-%m-%dT%H:%M:%S.000+00:00")
    ret["last_updated"] = velho

    async def _sh_velho(sid):
        return {"id": sid, "status": "delivered", "status_history": {"date_delivered": velho}, "last_updated": velho}

    fake.get_shipment = _sh_velho
    chamadas = {"claim": 0}

    async def _claim_contando(cid):
        chamadas["claim"] += 1
        return claim

    fake.get_claim = _claim_contando
    info = await logistica_meli._return_info_for_pedido(fake, "2000012345")
    assert chamadas["claim"] == 0 and info.prazo_acao is None and not info.prazo_desconhecido
