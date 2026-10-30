"""Pedido do ML com reclamação que não vem em `order.mediations` — a Logística
procura a mediação pela busca de reclamações.

Caso real (Vinicius, 30/10, 301741, ML marquezini): venda 2000018829075218,
envio 48187290435. O ML mostrava "Mediação finalizada. Te demos o dinheiro";
a Logística mostrava "Cancelado | Enviado | Reclamação aberta | Mediação",
sem etapa, situação nem beneficiado da mediação. O "Reclamação aberta" é o
substatus `claimed_me` do envio, que o ML não tira quando a mediação acaba —
parecia aberta porque a parte que diz "fechada" não foi lida."""

from __future__ import annotations

import pytest

from app.services import logistica_meli, logistica_rules
from app.services.marketplaces.ml import MercadoLivreClient
from tests.test_logistica_meli import FakeML

PEDIDO = 2000018829075218
ENVIO = 48187290435

_MEDIACAO_FECHADA_VENDEDOR = {
    "stage": "dispute",
    "status": "closed",
    "last_updated": "2026-10-27T05:01:00.000-03:00",
    "resolution": {
        "benefited": ["respondent"],
        "date_created": "2026-10-27T05:01:00.000-03:00",
    },
}


class FakeMLBusca(FakeML):
    """FakeML com a busca de reclamações por recurso: `busca` = {(recurso,
    id): [claims]}; `falha` = recursos cuja busca levanta."""

    def __init__(self, *a, busca=None, falha=(), **kw):
        super().__init__(*a, **kw)
        self._busca = busca or {}
        self._falha = set(falha)
        self.buscas: list[tuple[str, str]] = []

    async def buscar_reclamacoes_do_recurso(self, resource, resource_id):
        self.buscas.append((resource, str(resource_id)))
        if resource in self._falha:
            raise RuntimeError("400 invalid resource")
        return self._busca.get((resource, str(resource_id)), [])


def _pedido_301741():
    return {
        "id": PEDIDO,
        "status": "cancelled",
        "cancel_detail": {"group": "mediations"},
        "shipping": {"id": ENVIO},
        "mediations": [],
    }


@pytest.mark.asyncio
async def test_caso_301741_mediacao_achada_pelo_envio_mostra_fechada():
    client = FakeMLBusca(
        order=_pedido_301741(),
        shipment={"status": "shipped", "substatus": "claimed_me"},
        claims_by_id={5599000001: _MEDIACAO_FECHADA_VENDEDOR},
        busca={("shipment", str(ENVIO)): [
            {"id": 5599000001, "resource": "shipment", "resource_id": ENVIO},
        ]},
    )
    out = await logistica_meli.build_meli_status(client, str(PEDIDO))
    assert out["claim_stage"] == "dispute"
    assert out["claim_status"] == "closed"
    assert out["benefited"] == "respondent"
    assert logistica_rules.assinatura_pt(out) == (
        "Cancelado | Enviado | Reclamação aberta | Mediação | Mediação | Fechada | Vendedor"
    )
    # Procurou primeiro pelo pedido, depois pelo envio.
    assert client.buscas == [("order", str(PEDIDO)), ("shipment", str(ENVIO))]


@pytest.mark.asyncio
async def test_achada_pelo_pedido_nao_procura_pelo_envio():
    client = FakeMLBusca(
        order=_pedido_301741(),
        shipment={"status": "shipped", "substatus": "claimed_me"},
        claims_by_id={77: _MEDIACAO_FECHADA_VENDEDOR},
        busca={("order", str(PEDIDO)): [
            {"id": 77, "resource": "order", "resource_id": PEDIDO},
        ]},
    )
    out = await logistica_meli.build_meli_status(client, str(PEDIDO))
    assert out["claim_status"] == "closed"
    assert client.buscas == [("order", str(PEDIDO))]


@pytest.mark.asyncio
async def test_so_o_envio_reclamado_ja_e_sinal():
    # Pedido pago (não cancelado), envio marcado "reclamado": também procura.
    pedido = {"id": 1, "status": "paid", "shipping": {"id": 9}, "mediations": []}
    client = FakeMLBusca(
        order=pedido,
        shipment={"status": "shipped", "substatus": "claimed_me"},
        claims_by_id={5: {"stage": "claim", "status": "opened"}},
        busca={("order", "1"): [{"id": 5, "resource": "order", "resource_id": 1}]},
    )
    out = await logistica_meli.build_meli_status(client, "1")
    assert out["claim_status"] == "opened"


@pytest.mark.asyncio
async def test_sem_sinal_de_reclamacao_nao_procura():
    # O comum: pago, enviado, sem mediação — nenhuma chamada a mais.
    client = FakeMLBusca(
        order={"id": 1, "status": "paid", "shipping": {"id": 5}, "mediations": []},
        shipment={"status": "shipped", "substatus": "dropped_off"},
    )
    out = await logistica_meli.build_meli_status(client, "1")
    assert "claim_status" not in out
    assert client.buscas == []


@pytest.mark.asyncio
async def test_mediacao_listada_no_pedido_nao_procura():
    pedido = _pedido_301741() | {"mediations": [{"id": 77}]}
    client = FakeMLBusca(
        order=pedido,
        shipment={"status": "shipped", "substatus": "claimed_me"},
        claims_by_id={77: _MEDIACAO_FECHADA_VENDEDOR},
    )
    out = await logistica_meli.build_meli_status(client, str(PEDIDO))
    assert out["claim_status"] == "closed"
    assert client.buscas == []


@pytest.mark.asyncio
async def test_busca_que_falha_nao_derruba_a_leitura():
    # A busca pelo pedido levanta; a do envio não acha nada: fica como antes.
    client = FakeMLBusca(
        order=_pedido_301741(),
        shipment={"status": "shipped", "substatus": "claimed_me"},
        falha={"order"},
    )
    out = await logistica_meli.build_meli_status(client, str(PEDIDO))
    assert out == {
        "order_status": "cancelled",
        "ship_status": "shipped",
        "ship_substatus": "claimed_me",
        "cancel_group": "mediations",
    }
    assert client.buscas == [("order", str(PEDIDO)), ("shipment", str(ENVIO))]


class _Resp:
    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        return None

    def json(self):
        return self._body


class _ClienteBusca:
    """Só o `_request` que o método da busca usa."""

    def __init__(self, body):
        self._body = body
        self.params = None

    async def _request(self, method, path, **kwargs):
        self.params = kwargs.get("params")
        return _Resp(self._body)


@pytest.mark.asyncio
async def test_busca_por_recurso_descarta_reclamacao_de_outro_pedido():
    # Se o ML ignorar o filtro e devolver reclamações da conta inteira, só a
    # do recurso pedido entra — nunca a mediação de outro pedido na linha.
    cli = _ClienteBusca({"data": [
        {"id": 1, "resource": "shipment", "resource_id": ENVIO},
        {"id": 2, "resource": "shipment", "resource_id": 999},
        {"id": 3, "resource": "order", "resource_id": ENVIO},
    ]})
    achadas = await MercadoLivreClient.buscar_reclamacoes_do_recurso(
        cli, "shipment", str(ENVIO)
    )
    assert [c["id"] for c in achadas] == [1]
    assert cli.params == {"resource": "shipment", "resource_id": str(ENVIO), "limit": 50}
