"""De onde sai a data de cada campo do Status Plataforma, canal por canal.

Complementa o test_logistica_datas (regra de prioridade, sem canal): aqui os
clients falsos devolvem os payloads REAIS de cada marketplace e travamos de
qual campo do payload sai a data — se o ML mudar `status_history`, ou a Shopee
o `update_time`, o teste cai antes de virar "data errada na tela".

Também cobre o `detalhe_para`: a assinatura aberta linha a linha (é o que o
balãozinho mostra), que é POR PLATAFORMA — a Shopee tem logistics_status, a
Amazon easyship_status, e nenhum dos dois existe no vocabulário do Meli."""

from __future__ import annotations

import pytest

from app.services import (
    logistica_amazon,
    logistica_datas,
    logistica_meli,
    logistica_rules,
    logistica_shopee,
    logistica_tiktok,
)
from tests.test_logistica_amazon import FakeAmazon
from tests.test_logistica_meli import FakeML
from tests.test_logistica_shopee import FakeShopee


@pytest.mark.asyncio
async def test_ml_data_do_envio_vem_do_status_history():
    # O ML data CADA status do envio em status_history — data oficial, sem
    # chamada extra. Já o SUBstatus ele não data: fica a última mexida no envio.
    client = FakeML(
        order={
            "status": "paid", "shipping": {"id": 5}, "mediations": [],
            "last_updated": "2026-08-19T10:00:00Z",
        },
        shipment={
            "status": "not_delivered",
            "substatus": "confiscated",
            "last_updated": "2026-08-18T03:11:00Z",
            "status_history": {
                "date_shipped": "2026-08-11T19:22:00Z",
                "date_not_delivered": "2026-08-15T07:40:00Z",
                "date_delivered": None,
            },
        },
    )
    enr = await logistica_meli.build_enrichment(client, "1")
    datas = enr["datas"]
    assert datas["ship_status"] == {"em": "2026-08-15T07:40:00+00:00", "fonte": "plataforma"}
    assert datas["ship_substatus"] == {"em": "2026-08-18T03:11:00+00:00", "fonte": "aprox"}


@pytest.mark.asyncio
async def test_ml_cancelamento_usa_a_data_do_cancel_detail():
    client = FakeML(
        order={
            "status": "cancelled",
            "cancel_detail": {"group": "shipping", "date": "2026-08-12T08:05:00Z"},
            "date_closed": "2026-08-01T00:00:00Z",
            "last_updated": "2026-08-19T10:00:00Z",
            "shipping": {},
            "mediations": [],
        },
    )
    enr = await logistica_meli.build_enrichment(client, "1")
    esperado = {"em": "2026-08-12T08:05:00+00:00", "fonte": "plataforma"}
    # Pedido e "quem cancelou" mudaram no mesmo instante: o do cancelamento.
    assert enr["datas"]["order_status"] == esperado
    assert enr["datas"]["cancel_group"] == esperado


@pytest.mark.asyncio
async def test_ml_sem_data_no_payload_nao_inventa():
    # Payload seco (o mais comum nos pedidos antigos): sem data, o campo fica
    # sem proposta e quem carimba é o merge (fonte davinci).
    client = FakeML(
        order={"status": "paid", "shipping": {"id": 5}, "mediations": []},
        shipment={"status": "shipped", "substatus": "dropped_off"},
    )
    enr = await logistica_meli.build_enrichment(client, "1")
    assert enr["datas"] == {}


@pytest.mark.asyncio
async def test_shopee_datas_do_pedido_e_do_ultimo_evento():
    client = FakeShopee(
        {"2504ABC": "SHIPPED"},
        info_by_sn={
            "2504ABC": {
                "logistics_status": "LOGISTICS_PICKUP_DONE",
                "tracking_info": [
                    {"update_time": 1_755_000_000, "description": "Pedido em preparação"},
                    {"update_time": 1_755_100_000, "description": "Pedido postado"},
                ],
            }
        },
    )
    enr = await logistica_shopee.build_enrichment(client, "2504ABC")
    # O FakeShopee manda update_time=None no pedido → só o físico tem data.
    assert enr["datas"]["logistics_status"] == {
        "em": logistica_datas.iso(1_755_100_000),
        "fonte": "plataforma",
    }


@pytest.mark.asyncio
async def test_tiktok_data_vem_do_update_time_do_pedido():
    class FakeTikTok:
        async def get_order_detail(self, order_id):
            return {"status": "IN_TRANSIT", "update_time": 1_755_000_000, "tracking_number": "TT1"}

        async def get_tracking(self, order_id):
            return {"tracking": []}

    enr = await logistica_tiktok.build_enrichment(FakeTikTok(), "577")
    assert enr["datas"]["order_status"] == {
        "em": logistica_datas.iso(1_755_000_000),
        "fonte": "plataforma",
    }


@pytest.mark.asyncio
async def test_amazon_so_data_o_pedido_inteiro_entao_e_aproximada():
    client = FakeAmazon(
        {
            "701-1": {
                "order_status": "Shipped",
                "easyship_status": "OutForDelivery",
                "last_update_date": "2026-08-18T12:00:00Z",
            }
        }
    )
    enr = await logistica_amazon.build_enrichment(client, "701-1")
    for campo in ("order_status", "easyship_status"):
        assert enr["datas"][campo] == {"em": "2026-08-18T12:00:00+00:00", "fonte": "aprox"}


def test_detalhe_para_abre_a_assinatura_de_cada_plataforma():
    ml = logistica_rules.detalhe_para(
        "Mercado Livre",
        {"order_status": "cancelled", "ship_status": "not_delivered",
         "ship_substatus": "confiscated", "cancel_group": "shipping"},
    )
    assert [x["rotulo"] for x in ml] == [
        "Status do pedido", "Status do envio", "Substatus do envio", "Quem cancelou",
    ]
    # Mesmos valores PT da assinatura achatada — o balãozinho não pode divergir
    # da coluna.
    assert " | ".join(x["valor"] for x in ml) == logistica_rules.assinatura_para(
        "Mercado Livre",
        {"order_status": "cancelled", "ship_status": "not_delivered",
         "ship_substatus": "confiscated", "cancel_group": "shipping"},
    )

    shopee = logistica_rules.detalhe_para(
        "Shopee", {"order_status": "CANCELLED", "logistics_status": "LOGISTICS_DELIVERY_DONE"}
    )
    assert shopee == [
        {"campo": "order_status", "rotulo": "Status do pedido", "valor": "Cancelado"},
        {"campo": "logistics_status", "rotulo": "Status do envio", "valor": "Entregue"},
    ]

    amazon = logistica_rules.detalhe_para(
        "Amazon", {"order_status": "Shipped", "easyship_status": "OutForDelivery"}
    )
    assert [x["valor"] for x in amazon] == ["Enviado", "Saiu p/ entrega"]

    assert logistica_rules.detalhe_para("Shopee", {}) == []
