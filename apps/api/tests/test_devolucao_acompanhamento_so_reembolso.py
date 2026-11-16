"""Aba Acompanhamento × caso SÓ reembolso do TikTok (`return_type=REFUND`).

Caso real 294865 (Vinicius, 16/09): o cliente cancelou a devolução e abriu
reembolso sem devolver; a aba chamou de devolução e, quando o TikTok pagou
por prazo, carimbou "Chegou em" — sem pacote nenhum. Funções puras do router,
sem banco (mesmo estilo de test_devolucao_pacote_entregue)."""

from __future__ import annotations

from datetime import UTC, datetime

from app.routers.devolutions import _chegou_em, _com_status_da_devolucao

PAGO_EM = datetime(2026, 9, 16, 2, 29, 54, tzinfo=UTC)
DATAS = {"return_status": {"em": "2026-09-16T02:29:54+00:00", "fonte": "davinci"}}
COMPLETE = "RETURN_OR_REFUND_REQUEST_COMPLETE"


def test_so_reembolso_pago_nao_carimba_chegada_por_nenhum_caminho():
    """Linha da Logística ANTIGA (só status, sem tipo) + sync dizendo REFUND:
    nem o caminho da Logística nem o do sync podem dizer que chegou."""
    assert _chegou_em(
        plataforma="TikTok",
        meli_status={"order_status": "COMPLETED", "return_status": COMPLETE},
        status_datas=DATAS,
        devolucao_status_auto=COMPLETE,
        fonte_auto="tiktok",
        devolucao_atualizada_em=PAGO_EM,
        pacote_entregue_em=None,
        devolucao_tipo_auto="REFUND",
    ) is None


def test_devolucao_reembolsada_com_pacote_na_rua_nao_carimba_chegada():
    """301193 (05/11): o TikTok aprovou sozinho e pagou o cliente, com a
    logística "Em trânsito". Caso fechado não é pacote recebido."""
    caso = {
        "plataforma": "TikTok",
        "meli_status": {"order_status": "COMPLETED", "return_status": COMPLETE},
        "status_datas": DATAS,
        "devolucao_status_auto": COMPLETE,
        "fonte_auto": "tiktok",
        "devolucao_atualizada_em": PAGO_EM,
    }
    assert _chegou_em(**caso, devolucao_tipo_auto="RETURN_AND_REFUND") is None
    # Sem tipo (sync antigo) também não.
    assert _chegou_em(**caso) is None


def test_devolucao_tiktok_chega_pelo_rastreio_mesmo_depois_de_reembolsada():
    """A entrega confirmada pelo rastreio manda, com a data dela, e não a do
    reembolso."""
    chegou = _chegou_em(
        plataforma="TikTok",
        meli_status={"order_status": "COMPLETED", "return_status": COMPLETE},
        status_datas=DATAS,
        devolucao_status_auto=COMPLETE,
        fonte_auto="tiktok",
        devolucao_atualizada_em=PAGO_EM,
        pacote_entregue_em=datetime(2026, 9, 20, 15, 0, tzinfo=UTC),
        devolucao_tipo_auto="RETURN_AND_REFUND",
    )
    assert chegou is not None and chegou.isoformat() == "2026-09-20"


def test_localizacao_fala_de_reembolso_e_nao_de_devolucao():
    d = _com_status_da_devolucao(
        {"localizacao": "Entregue"},
        localizacao_manual=None,
        lg_plataforma="TikTok",
        lg_meli_status={"order_status": "DELIVERED", "return_status": "RETURN_OR_REFUND_REQUEST_PENDING"},
        status_auto="RETURN_OR_REFUND_REQUEST_PENDING",
        fonte_auto="tiktok",
        localizacao_auto=None,
        pacote_entregue_em=None,
        tipo_auto="REFUND",
    )
    assert d["localizacao"] == "Reembolso solicitado — responder no TikTok"
    assert d["entrega_localizacao"] == "Entregue"


def test_sem_tipo_no_sync_cai_no_texto_de_devolucao():
    d = _com_status_da_devolucao(
        {"localizacao": "Entregue"},
        localizacao_manual=None,
        lg_plataforma="TikTok",
        lg_meli_status={"order_status": "DELIVERED"},
        status_auto="BUYER_SHIPPED_ITEM",
        fonte_auto="tiktok",
        localizacao_auto=None,
        pacote_entregue_em=None,
    )
    assert d["localizacao"] == "Cliente enviou o item de volta"


def test_ml_cancelado_com_pacote_de_volta_carimba_chegada_pelo_returned():
    """287876 (18/09): cancelamento por não entrega — sem claim, estorno em
    02/08 e o envio de ida `returned` em 17/09. O sync deixa o tipo vazio
    (logistica_meli._com_pacote_voltando) e a chegada vem do `returned`."""
    from app.routers.devolutions import _fila

    meli = {
        "order_status": "cancelled",
        "cancel_group": "internal",
        "ship_status": "not_delivered",
        "ship_substatus": "returned",
    }
    datas = {"ship_substatus": {"em": "2026-09-17T19:20:06.374000+00:00", "fonte": "aprox"}}
    chegou = _chegou_em(
        plataforma="Mercado Livre",
        meli_status=meli,
        status_datas=datas,
        devolucao_status_auto=None,
        fonte_auto="ml",
        devolucao_atualizada_em=None,
        pacote_entregue_em=None,
        devolucao_tipo_auto=None,
    )
    assert chegou is not None and chegou.isoformat() == "2026-09-17"
    assert _fila(None, None) == ("acompanhamento", False)


def test_ml_com_devolucao_viva_na_logistica_nao_e_so_reembolso():
    """308486 (16/11/2026): a returns API do claim não trouxe nada (vira "REFUND",
    painel Fraude), mas a Logística já vê a devolução pronta pra envio pro galpão
    do ML — tem pacote: o tipo fica vazio (Acompanhamento)."""
    from types import SimpleNamespace

    from app.services import logistica_meli
    from app.services.devolucao_returns import ReturnInfo

    info = ReturnInfo(
        fonte="ml", status=None, tracking=None, carrier=None, created_at=None,
        updated_at=None, return_id="999", return_type="REFUND",
    )
    viva = SimpleNamespace(
        plataforma="Mercado Livre",
        meli_status={"ship_status": "delivered", "return_status": "ready_to_ship",
                     "return_destino": "warehouse"},
    )
    fechada = SimpleNamespace(
        plataforma="Mercado Livre",
        meli_status={"ship_status": "delivered", "return_status": "cancelled"},
    )
    sem = SimpleNamespace(plataforma="Mercado Livre", meli_status={"ship_status": "delivered"})
    assert logistica_meli._com_pacote_voltando(info, [viva]).return_type is None
    assert logistica_meli._com_pacote_voltando(info, [fechada]).return_type == "REFUND"
    assert logistica_meli._com_pacote_voltando(info, [sem]).return_type == "REFUND"
