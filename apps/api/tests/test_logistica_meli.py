"""logistica_meli — montagem da assinatura do Meli a partir da API do ML +
tradução PT. Usa um client falso (sem HTTP/DB) pra travar o mapeamento dos 8
campos e a resiliência quando não há reclamação/devolução."""

from __future__ import annotations

import pytest

from app.services import logistica_meli, logistica_rules


class FakeML:
    """Client ML mínimo: métodos que build_meli_status chama. `None` num
    recurso => a chamada levanta (simula 404 sem shipment/claim/returns)."""

    def __init__(self, order, shipment=None, claim=None, returns=None):
        self._order = order
        self._shipment = shipment
        self._claim = claim
        self._returns = returns

    async def get_order(self, order_id):
        return self._order

    async def get_shipment(self, shipment_id):
        if self._shipment is None:
            raise RuntimeError("no shipment")
        return self._shipment

    async def get_claim(self, claim_id):
        if self._claim is None:
            raise RuntimeError("no claim")
        return self._claim

    async def get_claim_returns(self, claim_id):
        if self._returns is None:
            raise RuntimeError("no returns")
        return self._returns


@pytest.mark.asyncio
async def test_build_meli_status_completo():
    client = FakeML(
        order={
            "status": "cancelled",
            "cancel_detail": {"group": "mediations"},
            "shipping": {"id": 123},
            "mediations": [{"id": 999}],
        },
        shipment={"status": "delivered", "substatus": "delivered"},
        claim={"stage": "dispute", "status": "closed", "resolution": {"benefited": ["complainant"]}},
        returns={"shipping": {"status": "shipped"}},
    )
    out = await logistica_meli.build_meli_status(client, "2000012345")
    assert out == {
        "order_status": "cancelled",
        "ship_status": "delivered",
        "ship_substatus": "delivered",
        "cancel_group": "mediations",
        "return_status": "shipped",
        "claim_stage": "dispute",
        "claim_status": "closed",
        "benefited": "complainant",
    }
    # Ordem fixa dos campos preservada (chave -> FIELD_ORDER).
    assert list(out.keys()) == [f for f in logistica_rules.FIELD_ORDER if f in out]


@pytest.mark.asyncio
async def test_build_meli_status_sem_reclamacao():
    # Pedido pago/enviado sem mediação: só pedido + envio; claim/returns fora.
    client = FakeML(
        order={"status": "paid", "shipping": {"id": 5}, "mediations": []},
        shipment={"status": "shipped", "substatus": "dropped_off"},
    )
    out = await logistica_meli.build_meli_status(client, "1")
    assert out == {
        "order_status": "paid",
        "ship_status": "shipped",
        "ship_substatus": "dropped_off",
    }


@pytest.mark.asyncio
async def test_build_meli_status_returns_lista():
    # returns pode vir como lista — pega o [0].shipping.status.
    client = FakeML(
        order={"status": "cancelled", "shipping": {"id": 9}, "mediations": [{"id": 1}]},
        shipment={"status": "not_delivered", "substatus": ""},
        claim={"stage": "claim", "status": "opened"},
        returns=[{"shipping": {"status": "ready_to_ship"}}],
    )
    out = await logistica_meli.build_meli_status(client, "1")
    assert out["return_status"] == "ready_to_ship"
    assert out["claim_stage"] == "claim"
    assert out["claim_status"] == "opened"
    assert "benefited" not in out  # sem resolution


def test_assinatura_pt_traduz_na_ordem():
    meli = {
        "order_status": "cancelled",
        "ship_status": "delivered",
        "ship_substatus": "delivered",
        "cancel_group": "mediations",
        "return_status": "shipped",
        "claim_stage": "dispute",
        "claim_status": "closed",
        "benefited": "complainant",
    }
    assert logistica_rules.assinatura_pt(meli) == (
        "Cancelado | Entregue | Entregue | Mediação | Enviado | Mediação | Fechada | Comprador"
    )


def test_assinatura_pt_pula_vazios_e_mantem_ordem():
    meli = {"ship_status": "shipped", "order_status": "paid"}
    # ordem = FIELD_ORDER (order_status antes de ship_status), não a de inserção.
    assert logistica_rules.assinatura_pt(meli) == "Pago | Enviado"
    assert logistica_rules.assinatura_pt({}) == ""


def test_traduzir_valor_fallback_token_cru():
    assert logistica_rules.traduzir_valor("ship_substatus", "token_novo_do_ml") == "token_novo_do_ml"
    assert logistica_rules.traduzir_valor("order_status", "") == ""
