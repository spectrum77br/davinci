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


def test_devolucao_com_pacote_continua_carimbando():
    chegou = _chegou_em(
        plataforma="TikTok",
        meli_status={"order_status": "COMPLETED", "return_status": COMPLETE},
        status_datas=DATAS,
        devolucao_status_auto=COMPLETE,
        fonte_auto="tiktok",
        devolucao_atualizada_em=PAGO_EM,
        pacote_entregue_em=None,
        devolucao_tipo_auto="RETURN_AND_REFUND",
    )
    assert chegou is not None and chegou.isoformat() == "2026-09-15"
    # Sem tipo (sync antigo) vale o de sempre.
    assert _chegou_em(
        plataforma="TikTok",
        meli_status={"order_status": "COMPLETED", "return_status": COMPLETE},
        status_datas=DATAS,
        devolucao_status_auto=COMPLETE,
        fonte_auto="tiktok",
        devolucao_atualizada_em=PAGO_EM,
    ) is not None


def test_localizacao_fala_de_reembolso_e_nao_de_devolucao():
    d = _com_status_da_devolucao(
        {"localizacao": "Package has been delivered!"},
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
    assert d["entrega_localizacao"] == "Package has been delivered!"


def test_sem_tipo_no_sync_cai_no_texto_de_devolucao():
    d = _com_status_da_devolucao(
        {"localizacao": "Package has been delivered!"},
        localizacao_manual=None,
        lg_plataforma="TikTok",
        lg_meli_status={"order_status": "DELIVERED"},
        status_auto="BUYER_SHIPPED_ITEM",
        fonte_auto="tiktok",
        localizacao_auto=None,
        pacote_entregue_em=None,
    )
    assert d["localizacao"] == "Cliente enviou o item de volta"
