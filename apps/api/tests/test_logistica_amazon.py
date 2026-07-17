"""logistica_amazon — montagem da assinatura da Amazon (OrderStatus + EasyShip) +
localização proxy + divergência. Client falso (sem HTTP/DB) pra travar o
mapeamento, o despacho por-plataforma e o cruzamento comercial × físico."""

from __future__ import annotations

import pytest

from app.services import logistica_amazon, logistica_rules


class FakeAmazon:
    """Client Amazon mínimo: só o get_order_status que build_enrichment usa.
    `status_by_id` = {order_id: {order_status, easyship_status, ship_city,
    ship_state}} (ausente => None)."""

    def __init__(self, status_by_id: dict[str, dict]):
        self._status = status_by_id

    async def get_order_status(self, order_id):
        return self._status.get(str(order_id))


@pytest.mark.asyncio
async def test_build_enrichment_completo():
    client = FakeAmazon(
        {
            "701-2862299-1963401": {
                "order_status": "Shipped",
                "easyship_status": "OutForDelivery",
                "ship_city": "São Paulo",
                "ship_state": "SP",
            }
        }
    )
    enr = await logistica_amazon.build_enrichment(client, "701-2862299-1963401")
    assert enr["meli_status"] == {
        "order_status": "Shipped",
        "easyship_status": "OutForDelivery",
    }
    assert enr["rastreio"] is None
    # Localização proxy = easyship PT + destino.
    assert enr["localizacao"] == "Saiu p/ entrega → São Paulo/SP"


@pytest.mark.asyncio
async def test_build_enrichment_sem_easyship_nem_endereco():
    client = FakeAmazon({"X": {"order_status": "Pending"}})
    enr = await logistica_amazon.build_enrichment(client, "X")
    assert enr["meli_status"] == {"order_status": "Pending"}
    assert enr["localizacao"] is None


@pytest.mark.asyncio
async def test_build_enrichment_pedido_ausente_fica_vazio():
    client = FakeAmazon({})
    enr = await logistica_amazon.build_enrichment(client, "000")
    assert enr == {"meli_status": {}, "rastreio": None, "localizacao": None}


def test_assinatura_amazon_traduz():
    m = {"order_status": "Shipped", "easyship_status": "Delivered"}
    assert logistica_rules.assinatura_amazon(m) == "Enviado | Entregue"
    # Só order_status.
    assert logistica_rules.assinatura_amazon({"order_status": "Canceled"}) == "Cancelado"
    assert logistica_rules.assinatura_amazon({}) == ""
    assert logistica_rules.assinatura_amazon(None) == ""


def test_assinatura_para_despacha_amazon():
    m = {"order_status": "Shipped", "easyship_status": "Delivered"}
    assert logistica_rules.assinatura_para("Amazon", m) == "Enviado | Entregue"


def test_divergencia_amazon_entregue_mas_cancelado():
    d = logistica_rules.detectar_divergencia_amazon(
        {"order_status": "Canceled", "easyship_status": "Delivered"}
    )
    assert d is not None
    assert "Cliente recebeu" in d


def test_divergencia_amazon_enviado_mas_problema():
    d = logistica_rules.detectar_divergencia_amazon(
        {"order_status": "Shipped", "easyship_status": "ReturnedToSeller"}
    )
    assert d is not None
    assert "físico mostra problema" in d


def test_divergencia_amazon_sem_easyship():
    # Sem easyship (sinal físico) => None.
    assert logistica_rules.detectar_divergencia_amazon({"order_status": "Canceled"}) is None
    # Batem (entregue + shipped) => None.
    assert (
        logistica_rules.detectar_divergencia_amazon(
            {"order_status": "Shipped", "easyship_status": "Delivered"}
        )
        is None
    )
    assert logistica_rules.detectar_divergencia_amazon(None) is None
