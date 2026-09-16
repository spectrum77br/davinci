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
