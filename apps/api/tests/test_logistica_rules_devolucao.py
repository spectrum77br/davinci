"""`logistica_rules.devolucao_status_pt` — texto da devolução VIVA na aba
Acompanhamento (Eduardo 03/09). Função pura, sem banco."""

from __future__ import annotations

from app.services.logistica_rules import devolucao_status_pt


def test_shopee_viva_e_encerrada():
    assert devolucao_status_pt("Shopee", {"return_status": "PROCESSING"}) == (
        "Devolução em processamento (Shopee)"
    )
    assert devolucao_status_pt("shopee", {"return_status": "JUDGING"}) == (
        "Devolução em análise pela Shopee"
    )
    assert devolucao_status_pt("Shopee", {"return_status": "CANCELLED"}) is None
    assert devolucao_status_pt("Shopee", {"return_status": "CLOSED"}) is None
    # vivo sem tradução → nunca esconde
    assert devolucao_status_pt("Shopee", {"return_status": "NOVO_X"}) == "Devolução: NOVO_X"


def test_tiktok_viva_e_encerrada():
    assert devolucao_status_pt("TikTok", {"return_status": "BUYER_SHIPPED_ITEM"}) == (
        "Cliente enviou o item de volta"
    )
    assert devolucao_status_pt("tiktok shop", {"return_status": "AWAITING_BUYER_SHIP"}) == (
        "Devolução aprovada — aguardando o cliente enviar"
    )
    rejeitada = {"return_status": "REFUND_OR_RETURN_REQUEST_REJECT"}
    cancelada = {"return_status": "RETURN_OR_REFUND_REQUEST_CANCEL"}
    assert devolucao_status_pt("TikTok", rejeitada) is None
    assert devolucao_status_pt("TikTok", cancelada) is None


def test_ml_status_do_envio_da_devolucao():
    """ML (caso 291745): `ready_to_ship` = cliente ainda vai postar — a aba
    mostrava a ENTREGA original ("Entregue → Curitiba/PR")."""
    assert devolucao_status_pt("Mercado Livre", {"return_status": "ready_to_ship"}) == (
        "Devolução aprovada — aguardando o cliente postar"
    )
    assert devolucao_status_pt("ml", {"return_status": "shipped"}) == (
        "Devolução a caminho (Mercado Envios)"
    )
    assert devolucao_status_pt("ml", {"return_status": "delivered"}) == (
        "Devolução entregue ao vendedor"
    )
    assert devolucao_status_pt("ml", {"return_status": "cancelled"}) is None


def test_data_entrada_estimada_pelo_carimbo_do_sinal():
    """Sem caso de devolução no marketplace (recusa/não entrega), o "Em
    devolução desde" vem do carimbo do campo que sinaliza o retorno."""
    from app.services.logistica_rules import data_entrada_devolucao_estimada as f

    ml = {
        "ship_status": "not_delivered", "cancel_group": "shipment",
        "order_status": "cancelled", "ship_substatus": "returning_to_sender",
    }
    datas = {
        "ship_status": {"em": "2026-08-21T10:00:00+00:00", "fonte": "plataforma"},
        "ship_substatus": {"em": "2026-08-22T10:00:00+00:00", "fonte": "aprox"},
        "order_status": {"em": "2026-08-22T12:00:00+00:00", "fonte": "plataforma"},
    }
    # o mais antigo entre os campos que SINALIZAM (ship_status não sinaliza)
    assert f("Mercado Livre", ml, datas) == "2026-08-22T10:00:00+00:00"
    # sem carimbo nenhum (linha enriquecida antes das datas) → None
    assert f("Mercado Livre", ml, {}) is None
    # entregue normal → nada sinaliza
    assert f("ml", {"order_status": "paid", "ship_status": "delivered"}, datas) is None

    sh = {"order_status": "CANCELLED", "logistics_status": "LOGISTICS_DELIVERY_FAILED"}
    sd = {
        "logistics_status": {"em": "2026-08-27T00:00:00+00:00", "fonte": "plataforma"},
        "order_status": {"em": "2026-08-28T00:00:00+00:00", "fonte": "plataforma"},
    }
    assert f("Shopee", sh, sd) == "2026-08-27T00:00:00+00:00"

    amz = {"order_status": "Shipped", "easyship_status": "ReturningToSeller"}
    amz_datas = {"easyship_status": {"em": "2026-08-21T20:34:23+00:00", "fonte": "aprox"}}
    assert f("Amazon", amz, amz_datas) == "2026-08-21T20:34:23+00:00"
    assert f("Amazon", {"order_status": "Shipped", "easyship_status": "Delivered"}, {}) is None
    assert f("Loja X", ml, datas) is None


def test_sem_devolucao_ou_plataforma_desconhecida():
    assert devolucao_status_pt("Shopee", {}) is None
    assert devolucao_status_pt("Shopee", None) is None
    assert devolucao_status_pt("Amazon", {"return_status": "X"}) is None
    assert devolucao_status_pt(None, {"return_status": "PROCESSING"}) is None
