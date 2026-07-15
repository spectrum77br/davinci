"""logistica_meli — montagem da assinatura do Meli a partir da API do ML +
tradução PT. Usa um client falso (sem HTTP/DB) pra travar o mapeamento dos 8
campos e a resiliência quando não há reclamação/devolução."""

from __future__ import annotations

import pytest

from app.services import logistica_meli, logistica_rules


class FakeML:
    """Client ML mínimo: métodos que build_meli_status chama. `None` num
    recurso => a chamada levanta (simula 404 sem shipment/claim/returns)."""

    def __init__(self, order, shipment=None, claim=None, returns=None, orders_by_id=None, pack=None, lead_time=None):
        self._order = order
        self._shipment = shipment
        self._claim = claim
        self._returns = returns
        # Quando setado, get_order resolve por id (id ausente => 404); habilita
        # o teste de fallback pack → order.
        self._orders_by_id = orders_by_id
        self._pack = pack
        # Payload do endpoint dedicado /shipments/{id}/lead_time (previsão).
        self._lead_time = lead_time

    async def get_order(self, order_id):
        if self._orders_by_id is not None:
            if str(order_id) not in self._orders_by_id:
                raise RuntimeError("Order do not exists")
            return self._orders_by_id[str(order_id)]
        return self._order

    async def get_pack(self, pack_id):
        if self._pack is None:
            raise RuntimeError("no pack")
        return self._pack

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

    async def _request(self, method, path, **kwargs):
        # Só o endpoint de lead_time é usado pelo enriquecimento. Sem payload
        # setado => 404 (simula shipment sem previsão dedicada).
        class _Resp:
            def __init__(self, status, body):
                self.status_code = status
                self._body = body

            def json(self):
                return self._body

        if path.endswith("/lead_time") and self._lead_time is not None:
            return _Resp(200, self._lead_time)
        return _Resp(404, {})


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
async def test_build_meli_status_resolve_pack_id():
    # O número guardado é um PACK id: /orders/{pack} 404 → resolve via /packs.
    client = FakeML(
        order=None,
        orders_by_id={
            "2000017409067996": {"status": "paid", "shipping": {"id": 5}, "mediations": []},
        },
        pack={"id": "2000014011101337", "orders": [{"id": 2000017409067996}]},
        shipment={"status": "shipped", "substatus": "dropped_off"},
    )
    out = await logistica_meli.build_meli_status(client, "2000014011101337")
    assert out == {
        "order_status": "paid",
        "ship_status": "shipped",
        "ship_substatus": "dropped_off",
    }


@pytest.mark.asyncio
async def test_build_enrichment_rastreio_do_shipment():
    # rastreio = tracking_number do shipment; meli_status monta normal.
    client = FakeML(
        order={"status": "paid", "shipping": {"id": 5}, "mediations": []},
        shipment={"status": "shipped", "substatus": "dropped_off", "tracking_number": "AP085672954BR"},
    )
    enr = await logistica_meli.build_enrichment(client, "1")
    assert enr["rastreio"] == "AP085672954BR"
    assert enr["meli_status"] == {
        "order_status": "paid",
        "ship_status": "shipped",
        "ship_substatus": "dropped_off",
    }


@pytest.mark.asyncio
async def test_build_enrichment_sem_tracking_number():
    # shipment sem tracking_number => rastreio None (não inventa).
    client = FakeML(
        order={"status": "paid", "shipping": {"id": 5}, "mediations": []},
        shipment={"status": "ready_to_ship", "substatus": "printed"},
    )
    enr = await logistica_meli.build_enrichment(client, "1")
    assert enr["rastreio"] is None


@pytest.mark.asyncio
async def test_build_enrichment_localizacao_do_substatus():
    # localizacao = substatus traduzido (proxy do último local); cai no status.
    client = FakeML(
        order={"status": "paid", "shipping": {"id": 5}, "mediations": []},
        shipment={"status": "shipped", "substatus": "out_for_delivery"},
    )
    enr = await logistica_meli.build_enrichment(client, "1")
    assert enr["localizacao"] == "Saiu p/ entrega"

    client2 = FakeML(
        order={"status": "paid", "shipping": {"id": 5}, "mediations": []},
        shipment={"status": "shipped", "substatus": ""},
    )
    enr2 = await logistica_meli.build_enrichment(client2, "1")
    assert enr2["localizacao"] == "Enviado"


@pytest.mark.asyncio
async def test_build_enrichment_localizacao_com_destino_e_previsao():
    # Rede própria: status + destino (receiver_address) + previsão (lead_time).
    client = FakeML(
        order={"status": "paid", "shipping": {"id": 5}, "mediations": []},
        shipment={
            "status": "shipped",
            "substatus": "out_for_delivery",
            "receiver_address": {"city": {"name": "São Paulo"}, "state": {"id": "BR-SP"}},
            "lead_time": {"estimated_delivery_final": {"date": "2026-07-16T00:00:00.000-03:00"}},
        },
    )
    enr = await logistica_meli.build_enrichment(client, "1")
    assert enr["localizacao"] == "Saiu p/ entrega → São Paulo/SP · previsão 16/07"


@pytest.mark.asyncio
async def test_build_enrichment_previsao_do_endpoint_dedicado():
    # get_shipment sem lead_time embutido + envio em curso => busca /lead_time.
    client = FakeML(
        order={"status": "paid", "shipping": {"id": 5}, "mediations": []},
        shipment={
            "status": "shipped",
            "substatus": "out_for_delivery",
            "receiver_address": {"city": {"name": "Recife"}, "state": {"id": "BR-PE"}},
        },
        lead_time={"estimated_delivery_limit": {"date": "2026-07-20T00:00:00.000-03:00"}},
    )
    enr = await logistica_meli.build_enrichment(client, "1")
    assert enr["localizacao"] == "Saiu p/ entrega → Recife/PE · previsão 20/07"


@pytest.mark.asyncio
async def test_build_enrichment_terminal_nao_busca_previsao():
    # Envio entregue (terminal) NÃO chama /lead_time mesmo que exista payload.
    client = FakeML(
        order={"status": "paid", "shipping": {"id": 5}, "mediations": []},
        shipment={
            "status": "delivered",
            "substatus": "delivered",
            "receiver_address": {"city": {"name": "Recife"}, "state": {"id": "BR-PE"}},
        },
        lead_time={"estimated_delivery_limit": {"date": "2026-07-20T00:00:00.000-03:00"}},
    )
    enr = await logistica_meli.build_enrichment(client, "1")
    assert enr["localizacao"] == "Entregue → Recife/PE"


def test_localizacao_completa_omite_partes_ausentes():
    assert logistica_rules.localizacao_completa("Enviado") == "Enviado"
    assert (
        logistica_rules.localizacao_completa("Enviado", destino="Recife/PE") == "Enviado → Recife/PE"
    )
    assert (
        logistica_rules.localizacao_completa("Enviado", previsao="20/07")
        == "Enviado · previsão 20/07"
    )
    assert logistica_rules.localizacao_completa("", destino="Recife/PE") == "Recife/PE"


def test_localizacao_pt_prioriza_substatus():
    assert logistica_rules.localizacao_pt(
        {"ship_status": "shipped", "ship_substatus": "dropped_off"}
    ) == "Entregue à agência"
    assert logistica_rules.localizacao_pt({"ship_status": "delivered"}) == "Entregue"
    assert logistica_rules.localizacao_pt({}) == ""


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
async def test_build_meli_status_returns_v2_shipments():
    # Formato real do endpoint v2: {id, shipments:[{status}]}.
    client = FakeML(
        order={"status": "paid", "shipping": {"id": 7}, "mediations": [{"id": 2}]},
        shipment={"status": "delivered", "substatus": "delivered"},
        claim={"stage": "claim", "status": "opened"},
        returns={"id": 148419512, "shipments": [{"shipment_id": 47511095985, "status": "ready_to_ship"}]},
    )
    out = await logistica_meli.build_meli_status(client, "1")
    assert out["return_status"] == "ready_to_ship"
    assert out["claim_stage"] == "claim"


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
