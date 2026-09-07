"""logistica_meli — montagem da assinatura do Meli a partir da API do ML +
tradução PT. Usa um client falso (sem HTTP/DB) pra travar o mapeamento dos 8
campos e a resiliência quando não há reclamação/devolução."""

from __future__ import annotations

import pytest

from app.services import logistica_meli, logistica_rules


class FakeML:
    """Client ML mínimo: métodos que build_meli_status chama. `None` num
    recurso => a chamada levanta (simula 404 sem shipment/claim/returns)."""

    def __init__(
        self,
        order,
        shipment=None,
        claim=None,
        returns=None,
        orders_by_id=None,
        pack=None,
        lead_time=None,
        claims_by_id=None,
    ):
        self._order = order
        self._shipment = shipment
        self._claim = claim
        self._returns = returns
        # Quando setado, get_claim resolve por id (id ausente => 404); habilita
        # o teste de pedido com mais de uma mediação.
        self._claims_by_id = claims_by_id
        self.claims_lidos: list = []
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
        self.claims_lidos.append(claim_id)
        if self._claims_by_id is not None:
            if claim_id not in self._claims_by_id:
                raise RuntimeError("Claim not found")
            return self._claims_by_id[claim_id]
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
async def test_build_meli_status_varias_mediacoes_vale_a_mais_recente():
    # Caso real 2000018106772396 (07/09): a 1ª mediação (listada primeiro)
    # fechou a favor do vendedor em 03/09; a 2ª fechou a favor do comprador em
    # 06/09 com reembolso. Beneficiado tem que refletir a MAIS RECENTE.
    client = FakeML(
        order={
            "status": "partially_refunded",
            "shipping": {"id": 1},
            "mediations": [{"id": 5569968766}, {"id": 5571329733}],
        },
        shipment={"status": "delivered", "substatus": "delivered"},
        claims_by_id={
            5569968766: {
                "stage": "dispute",
                "status": "closed",
                "last_updated": "2026-09-03T12:00:00.000-04:00",
                "resolution": {
                    "benefited": ["respondent"],
                    "date_created": "2026-09-03T12:00:00.000-04:00",
                },
            },
            5571329733: {
                "stage": "dispute",
                "status": "closed",
                "last_updated": "2026-09-06T09:00:00.000-04:00",
                "resolution": {
                    "benefited": ["complainant"],
                    "date_created": "2026-09-06T09:00:00.000-04:00",
                },
            },
        },
        returns=None,
    )
    out = await logistica_meli.build_meli_status(client, "2000018106772396")
    assert out["benefited"] == "complainant"
    assert out["claim_status"] == "closed"
    # Leu as duas — não decidiu pela ordem da lista.
    assert sorted(client.claims_lidos) == [5569968766, 5571329733]


@pytest.mark.asyncio
async def test_build_meli_status_varias_mediacoes_ordem_invertida():
    # Mesmo cenário com a mais recente listada PRIMEIRO: resultado idêntico.
    client = FakeML(
        order={"status": "paid", "shipping": {"id": 1}, "mediations": [{"id": 2}, {"id": 1}]},
        shipment={"status": "delivered", "substatus": "delivered"},
        claims_by_id={
            1: {
                "status": "closed",
                "resolution": {"benefited": ["respondent"], "date_created": "2026-09-01T00:00:00Z"},
            },
            2: {
                "status": "closed",
                "resolution": {"benefited": ["complainant"], "date_created": "2026-09-06T00:00:00Z"},
            },
        },
    )
    out = await logistica_meli.build_meli_status(client, "1")
    assert out["benefited"] == "complainant"


@pytest.mark.asyncio
async def test_build_meli_status_mediacao_com_falha_nao_derruba_as_outras():
    # Uma das mediações não pôde ser lida (404) — usa a que leu.
    client = FakeML(
        order={"status": "paid", "shipping": {"id": 1}, "mediations": [{"id": 404}, {"id": 2}]},
        shipment={"status": "delivered", "substatus": "delivered"},
        claims_by_id={2: {"status": "closed", "resolution": {"benefited": ["complainant"]}}},
    )
    out = await logistica_meli.build_meli_status(client, "1")
    assert out["benefited"] == "complainant"
    assert out["claim_status"] == "closed"


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


# ---- divergência ML × físico dos Correios --------------------------------


def test_divergencia_correios_entregou_ml_nao():
    # O caso real: ML consta cancelado/não entregue, mas os Correios mostram
    # entrega ao destinatário -> avisa que o cliente recebeu.
    meli = {"order_status": "cancelled", "ship_status": "not_delivered", "ship_substatus": "retained"}
    d = logistica_rules.detectar_divergencia(
        meli, "Eusebio/CE — Objeto entregue ao destinatário"
    )
    assert d is not None
    assert "entregue ao destinatário" in d.lower()
    assert "Cliente recebeu" in d


def test_divergencia_ml_entregue_correios_problema():
    meli = {"order_status": "paid", "ship_status": "delivered"}
    d = logistica_rules.detectar_divergencia(
        meli, "São Paulo/SP — Objeto devolvido ao remetente"
    )
    assert d is not None
    assert "físico mostra problema" in d


def test_divergencia_sem_divergencia_e_sem_sinal():
    # Ambos entregues -> None.
    assert (
        logistica_rules.detectar_divergencia(
            {"ship_status": "delivered"}, "Recife/PE — Objeto entregue ao destinatário"
        )
        is None
    )
    # Em trânsito não conta como divergência (só ruído) -> None.
    assert (
        logistica_rules.detectar_divergencia(
            {"ship_status": "shipped"}, "Curitiba/PR — Objeto em trânsito"
        )
        is None
    )
    # Sem localização física -> None.
    assert logistica_rules.detectar_divergencia({"ship_status": "not_delivered"}, "") is None
    assert logistica_rules.detectar_divergencia({"ship_status": "not_delivered"}, None) is None


# ---- enviar chamado (open-dispute + send-message ao mediador) -------------

from app.models import Logistica  # noqa: E402


def test_respondent_actions_extrai_do_player():
    claim = {
        "players": [
            {"role": "complainant", "available_actions": []},
            {
                "role": "respondent",
                "available_actions": [
                    {"action": "send_message_to_complainant"},
                    {"action": "open_dispute"},
                ],
            },
        ]
    }
    assert logistica_meli._respondent_actions(claim) == {
        "send_message_to_complainant",
        "open_dispute",
    }
    assert logistica_meli._respondent_actions({"players": []}) == set()


class FakeChamadoML:
    """Client que registra as chamadas de chamado (open-dispute/send-message)."""

    def __init__(self, order, claim, claims_by_id=None):
        self._order = order
        self._claim = claim
        self._claims_by_id = claims_by_id
        self.disputed: list = []
        self.messages: list = []

    async def get_order(self, order_id):
        return self._order

    async def get_pack(self, pack_id):
        raise RuntimeError("no pack")

    async def get_claim(self, claim_id):
        if self._claims_by_id is not None:
            return self._claims_by_id[claim_id]
        return self._claim

    async def open_claim_dispute(self, claim_id):
        self.disputed.append(claim_id)
        return {"id": claim_id}

    async def send_claim_message(self, claim_id, message, *, receiver_role="mediator", attachments=None):
        self.messages.append((claim_id, message, receiver_role))
        return {"ok": True}


def _patch_ml(monkeypatch, fake):
    monkeypatch.setattr(logistica_meli, "_ml_integration_for_conta", _fake_integ)
    monkeypatch.setattr(logistica_meli, "_build_ml_client", lambda session, integ: fake)


async def _fake_integ(session, conta):
    return object()  # integração não-nula; o client é injetado à parte


def _ml_row():
    return Logistica(plataforma="Mercado Livre", conta="loja", pedido_marketplace="ML1")


@pytest.mark.asyncio
async def test_enviar_chamado_abre_dispute_e_manda_ao_mediador(monkeypatch):
    fake = FakeChamadoML(
        order={"mediations": [{"id": 999}]},
        claim={
            "status": "opened",
            "players": [
                {"role": "respondent", "available_actions": [{"action": "open_dispute"}]}
            ],
        },
    )
    _patch_ml(monkeypatch, fake)
    row = _ml_row()
    cid = await logistica_meli.enviar_chamado_for_row(None, row, "oi ML")
    assert cid == "999"
    assert row.chamado == "999"
    assert fake.disputed == [999]  # escalou (não tinha send_message_to_mediator)
    assert fake.messages == [(999, "oi ML", "mediator")]


@pytest.mark.asyncio
async def test_enviar_chamado_ja_em_mediacao_nao_reabre(monkeypatch):
    fake = FakeChamadoML(
        order={"mediations": [{"id": 5}]},
        claim={
            "status": "opened",
            "players": [
                {"role": "respondent", "available_actions": [{"action": "send_message_to_mediator"}]}
            ],
        },
    )
    _patch_ml(monkeypatch, fake)
    cid = await logistica_meli.enviar_chamado_for_row(None, _ml_row(), "msg")
    assert cid == "5"
    assert fake.disputed == []  # já podia falar com o mediador → não reabre
    assert fake.messages == [(5, "msg", "mediator")]


@pytest.mark.asyncio
async def test_enviar_chamado_usa_a_mediacao_mais_recente(monkeypatch):
    # 1ª mediação (listada primeiro) já fechada; a 2ª, mais nova, está aberta:
    # a mensagem tem que ir pra 2ª — antes dava "reclamação encerrada".
    fake = FakeChamadoML(
        order={"mediations": [{"id": 1}, {"id": 2}]},
        claim=None,
        claims_by_id={
            1: {"status": "closed", "last_updated": "2026-09-03T00:00:00.000-04:00", "players": []},
            2: {
                "status": "opened",
                "last_updated": "2026-09-06T00:00:00.000-04:00",
                "players": [
                    {
                        "role": "respondent",
                        "available_actions": [{"action": "send_message_to_mediator"}],
                    }
                ],
            },
        },
    )
    _patch_ml(monkeypatch, fake)
    cid = await logistica_meli.enviar_chamado_for_row(None, _ml_row(), "msg")
    assert cid == "2"
    assert fake.messages == [(2, "msg", "mediator")]


@pytest.mark.asyncio
async def test_enviar_chamado_sem_reclamacao(monkeypatch):
    fake = FakeChamadoML(order={"mediations": []}, claim={})
    _patch_ml(monkeypatch, fake)
    with pytest.raises(logistica_meli.MeliEnrichError) as ei:
        await logistica_meli.enviar_chamado_for_row(None, _ml_row(), "msg")
    assert ei.value.code == "logistica_sem_reclamacao"


@pytest.mark.asyncio
async def test_enviar_chamado_reclamacao_encerrada(monkeypatch):
    fake = FakeChamadoML(
        order={"mediations": [{"id": 7}]},
        claim={"status": "closed", "players": [{"role": "respondent", "available_actions": []}]},
    )
    _patch_ml(monkeypatch, fake)
    with pytest.raises(logistica_meli.MeliEnrichError) as ei:
        await logistica_meli.enviar_chamado_for_row(None, _ml_row(), "msg")
    assert ei.value.code == "logistica_reclamacao_encerrada"
    assert fake.messages == []


@pytest.mark.asyncio
async def test_enviar_chamado_sem_acao_de_mediador(monkeypatch):
    # Aberta, mas o vendedor só pode falar com o comprador (nem mediador nem escalar).
    fake = FakeChamadoML(
        order={"mediations": [{"id": 8}]},
        claim={
            "status": "opened",
            "players": [
                {"role": "respondent", "available_actions": [{"action": "send_message_to_complainant"}]}
            ],
        },
    )
    _patch_ml(monkeypatch, fake)
    with pytest.raises(logistica_meli.MeliEnrichError) as ei:
        await logistica_meli.enviar_chamado_for_row(None, _ml_row(), "msg")
    assert ei.value.code == "logistica_reclamacao_sem_acao"
    assert fake.disputed == []
    assert fake.messages == []
