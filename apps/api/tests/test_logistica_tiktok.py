"""logistica_tiktok — montagem da assinatura do TikTok (status) + rastreio +
localização física (evento de tracking) + divergência. Client falso (sem HTTP/DB)
pra travar o mapeamento, o despacho por-plataforma e o cruzamento comercial ×
físico."""

from __future__ import annotations

import pytest

from app.services import logistica_rules, logistica_tiktok


class FakeTikTok:
    """Client TikTok mínimo: só o que build_enrichment usa.
    `orders` = {order_id: {status, tracking_number, recipient_address}};
    `tracking` = {order_id: {tracking: [{description, update_time_millis}]}}."""

    def __init__(self, orders: dict[str, dict], tracking: dict[str, dict] | None = None):
        self._orders = orders
        self._tracking = tracking or {}

    async def get_order_detail(self, order_id):
        return self._orders.get(str(order_id), {})

    async def get_tracking(self, order_id):
        return self._tracking.get(str(order_id), {})


@pytest.mark.asyncio
async def test_build_enrichment_completo():
    client = FakeTikTok(
        {"584947077397251291": {"status": "IN_TRANSIT", "tracking_number": "TT123BR"}},
        tracking={
            "584947077397251291": {
                "tracking": [
                    # fora de ordem de propósito: escolhe pelo maior update_time_millis.
                    {"update_time_millis": 100, "description": "Processed at the carrier's facility."},
                    {"update_time_millis": 200, "description": "Package dropped off with carrier."},
                ]
            }
        },
    )
    enr = await logistica_tiktok.build_enrichment(client, "584947077397251291")
    assert enr == {
        "meli_status": {"order_status": "IN_TRANSIT"},
        "rastreio": "TT123BR",
        "localizacao": "Package dropped off with carrier.",
        # Pedido sem update_time no payload => sem data proposta (quem carimba
        # nesse caso é o merge). Ver test_logistica_status_datas.
        "datas": {},
    }


@pytest.mark.asyncio
async def test_build_enrichment_sem_tracking_usa_destino():
    # Sem eventos de tracking, cai no destino do recipient_address.district_info.
    client = FakeTikTok(
        {
            "999": {
                "status": "AWAITING_COLLECTION",
                "recipient_address": {
                    "district_info": [
                        {"address_name": "Rondônia"},
                        {"address_name": "Porto Velho"},
                    ]
                },
            }
        }
    )
    enr = await logistica_tiktok.build_enrichment(client, "999")
    assert enr["meli_status"] == {"order_status": "AWAITING_COLLECTION"}
    assert enr["rastreio"] is None
    assert enr["localizacao"] == "Rondônia - Porto Velho"


@pytest.mark.asyncio
async def test_build_enrichment_pedido_ausente_fica_vazio():
    client = FakeTikTok({})
    enr = await logistica_tiktok.build_enrichment(client, "000")
    assert enr == {"meli_status": {}, "rastreio": None, "localizacao": None, "datas": {}}


def test_assinatura_tiktok_traduz():
    assert logistica_rules.assinatura_tiktok({"order_status": "IN_TRANSIT"}) == "Em trânsito"
    assert logistica_rules.assinatura_tiktok({"order_status": "DELIVERED"}) == "Entregue"
    # Token desconhecido cai no próprio valor, humanizado.
    assert logistica_rules.assinatura_tiktok({"order_status": "FOO_BAR"}) == "Foo Bar"
    assert logistica_rules.assinatura_tiktok({}) == ""
    assert logistica_rules.assinatura_tiktok(None) == ""


def test_assinatura_para_despacha_tiktok():
    tt = {"order_status": "COMPLETED"}
    assert logistica_rules.assinatura_para("TikTok", tt) == "Concluído"
    assert logistica_rules.assinatura_para("Tik Tok", tt) == "Concluído"


def test_divergencia_tiktok_entregue_mas_cancelado():
    d = logistica_rules.detectar_divergencia_tiktok(
        {"order_status": "CANCELLED"}, "Package delivered to recipient"
    )
    assert d is not None
    assert "Cliente recebeu" in d


def test_divergencia_tiktok_concluido_mas_problema():
    d = logistica_rules.detectar_divergencia_tiktok(
        {"order_status": "COMPLETED"}, "Package returned to sender"
    )
    assert d is not None
    assert "físico mostra problema" in d


def test_divergencia_tiktok_sem_sinal():
    # Sem localização física => None (conservador).
    assert logistica_rules.detectar_divergencia_tiktok({"order_status": "CANCELLED"}) is None
    # Em trânsito normal => None.
    assert (
        logistica_rules.detectar_divergencia_tiktok(
            {"order_status": "IN_TRANSIT"}, "In transit"
        )
        is None
    )
    assert logistica_rules.detectar_divergencia_tiktok(None, None) is None
