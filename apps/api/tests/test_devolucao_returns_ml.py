"""logistica_meli.returns_por_pedido — o pacote que VOLTA (devolução) de um
pedido ML, no contrato `devolucao_returns.ReturnInfo`.

Client ML falso (sem HTTP): order → mediations → returns do claim (v2) →
shipment do return. Integração/builder de client são monkeypatchados como em
test_logistica_meli; as linhas da Logística são semeadas no banco de teste."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.models import Logistica
from app.services import logistica_meli
from app.services.devolucao_returns import ReturnInfo


class FakeML:
    """Recursos por id; id ausente => levanta (simula 404 do ML). Registra as
    chamadas pra travar quantos shipments/claims foram buscados."""

    def __init__(self, *, orders=None, packs=None, claims=None, returns=None, shipments=None):
        self.orders = orders or {}
        self.packs = packs or {}
        self.claims = claims or {}
        self.returns = returns or {}
        self.shipments = shipments or {}
        self.calls: list[tuple[str, str]] = []

    def _get(self, kind: str, store: dict, key) -> dict | list:
        self.calls.append((kind, str(key)))
        try:
            return store[str(key)]
        except KeyError:
            raise RuntimeError(f"{kind} {key} not found (404)") from None

    async def get_order(self, order_id):
        return self._get("order", self.orders, order_id)

    async def get_pack(self, pack_id):
        return self._get("pack", self.packs, pack_id)

    async def get_claim(self, claim_id):
        return self._get("claim", self.claims, claim_id)

    async def get_claim_returns(self, claim_id):
        return self._get("returns", self.returns, claim_id)

    async def get_shipment(self, shipment_id):
        return self._get("shipment", self.shipments, shipment_id)

    def fetched(self, kind: str) -> list[str]:
        return [k for c, k in self.calls if c == kind]


@pytest.fixture
def patch_ml(monkeypatch):
    """Injeta o client falso: `contas` (lower) com integração ML; as outras
    resolvem None (sem integração)."""

    def _apply(fake: FakeML, *, contas: tuple[str, ...] = ("loja",)) -> FakeML:
        async def _integ(session, conta):
            return object() if (conta or "").strip().lower() in contas else None

        monkeypatch.setattr(logistica_meli, "_ml_integration_for_conta", _integ)
        # enrich/returns passam `lock=`; o fake ignora.
        monkeypatch.setattr(
            logistica_meli, "_build_ml_client", lambda session, integ, **kw: fake
        )
        return fake

    return _apply


def _row(pedido_bling: str, pedido_mk: str | None, *, conta="loja", plataforma="Mercado Livre"):
    return Logistica(
        data=date(2026, 8, 1),
        pedido_bling=pedido_bling,
        pedido_marketplace=pedido_mk,
        plataforma=plataforma,
        conta=conta,
    )


async def _seed(db, rows: list[Logistica]) -> list[Logistica]:
    db.add_all(rows)
    await db.commit()
    return rows


def _order(*claim_ids) -> dict:
    return {
        "status": "cancelled",
        "shipping": {"id": 1},
        "mediations": [{"id": c} for c in claim_ids],
    }


def _ret(
    shipment_id,
    status,
    *,
    created="2026-08-20T10:00:00.000-03:00",
    updated=None,
    ret_status="opened",
):
    ret = {
        "id": 148419512,
        "status": ret_status,
        "date_created": created,
        "shipments": [{"shipment_id": shipment_id, "status": status}],
    }
    if updated:
        ret["last_updated"] = updated
    return ret


@pytest.mark.asyncio
async def test_devolucao_viva_com_rastreio_mapeia_campos(db, patch_ml):
    fake = patch_ml(FakeML(
        orders={"ML1": _order(5001)},
        returns={"5001": _ret(9001, "shipped", updated="2026-08-21T10:00:00.000-03:00")},
        shipments={"9001": {
            "status": "shipped",
            "substatus": "in_transit",
            "tracking_number": "AA123456789BR",
            "tracking_method": "Correios",
            "date_created": "2026-08-20T11:00:00.000-03:00",
            "last_updated": "2026-08-22T12:00:00.000-03:00",
        }},
    ))
    rows = await _seed(db, [_row("B1", "ML1")])

    out = await logistica_meli.returns_por_pedido(db, rows)

    assert out == {
        "B1": ReturnInfo(
            fonte="ml",
            status="shipped",
            tracking="AA123456789BR",
            carrier="Correios",
            # date_created do return (UTC) / last_updated do shipment (a mais nova).
            created_at=datetime(2026, 8, 20, 13, 0, tzinfo=UTC),
            updated_at=datetime(2026, 8, 22, 15, 0, tzinfo=UTC),
            return_id="5001",
        )
    }
    # Return já datado => não gasta chamada no claim.
    assert fake.fetched("claim") == []
    assert fake.fetched("shipment") == ["9001"]


@pytest.mark.asyncio
async def test_devolucao_sem_rastreio_tracking_none(db, patch_ml):
    # Etiqueta ainda não gerada / só espaços => tracking None (nunca "").
    patch_ml(FakeML(
        orders={"ML1": _order(5001)},
        returns={"5001": _ret(9001, "ready_to_ship")},
        shipments={"9001": {"status": "ready_to_ship", "tracking_number": "   "}},
    ))
    rows = await _seed(db, [_row("B1", "ML1")])

    out = await logistica_meli.returns_por_pedido(db, rows)

    info = out["B1"]
    assert info.fonte == "ml"
    assert info.status == "ready_to_ship"
    assert info.tracking is None
    assert info.carrier is None
    assert info.return_id == "5001"
    assert info.created_at == datetime(2026, 8, 20, 13, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_varios_casos_vivo_mais_recente_vence(db, patch_ml):
    # 3 claims no pedido: 5001 cancelado (recente), 5002 vivo (mais antigo),
    # 5003 return fechado (o mais recente de todos) => vale o 5002 (vivo).
    fake = patch_ml(FakeML(
        orders={"ML1": _order(5001, 5002, 5003)},
        returns={
            "5001": _ret(9001, "cancelled", created="2026-08-25T10:00:00.000-03:00"),
            "5002": _ret(9002, "delivered", created="2026-08-10T10:00:00.000-03:00"),
            "5003": _ret(
                9003, "shipped", created="2026-08-28T10:00:00.000-03:00", ret_status="closed"
            ),
        },
        shipments={
            "9001": {"status": "cancelled", "tracking_number": "CANCEL1BR"},
            "9002": {"status": "delivered", "tracking_number": "VIVO2BR"},
            "9003": {"status": "shipped", "tracking_number": "FECHADO3BR"},
        },
    ))
    rows = await _seed(db, [_row("B1", "ML1")])

    out = await logistica_meli.returns_por_pedido(db, rows)

    assert out["B1"].return_id == "5002"
    assert out["B1"].tracking == "VIVO2BR"
    assert out["B1"].status == "delivered"
    # Só o shipment do caso escolhido é buscado.
    assert fake.fetched("shipment") == ["9002"]


@pytest.mark.asyncio
async def test_varios_casos_todos_mortos_vale_o_mais_recente(db, patch_ml):
    patch_ml(FakeML(
        orders={"ML1": _order(5001, 5002)},
        returns={
            "5001": _ret(9001, "cancelled", created="2026-08-25T10:00:00.000-03:00"),
            "5002": _ret(9002, "cancelled", created="2026-08-10T10:00:00.000-03:00"),
        },
        shipments={
            "9001": {"status": "cancelled", "tracking_number": "MAISNOVO1BR"},
            "9002": {"status": "cancelled", "tracking_number": "VELHO2BR"},
        },
    ))
    rows = await _seed(db, [_row("B1", "ML1")])

    out = await logistica_meli.returns_por_pedido(db, rows)

    assert out["B1"].return_id == "5001"
    assert out["B1"].tracking == "MAISNOVO1BR"
    assert out["B1"].status == "cancelled"


@pytest.mark.asyncio
async def test_returns_em_lista_e_empate_sem_data_desempata_pelo_claim(db, patch_ml):
    # Payload de returns como LISTA e sem datas: id do claim maior = mais novo.
    patch_ml(FakeML(
        orders={"ML1": _order(41, 42)},
        returns={
            "41": [{"id": 1, "shipments": [{"shipment_id": 9041, "status": "shipped"}]}],
            "42": [{"id": 2, "shipments": [{"shipment_id": 9042, "status": "shipped"}]}],
        },
        shipments={
            "9041": {"status": "shipped", "tracking_number": "A41BR"},
            "9042": {"status": "shipped", "tracking_number": "A42BR"},
        },
    ))
    rows = await _seed(db, [_row("B1", "ML1")])

    out = await logistica_meli.returns_por_pedido(db, rows)

    assert out["B1"].return_id == "42"
    assert out["B1"].tracking == "A42BR"


@pytest.mark.asyncio
async def test_sem_integracao_ou_erro_de_api_pula_sem_levantar(db, patch_ml):
    fake = patch_ml(FakeML(
        # ML2 não existe (nem como pack) => a API "falha" nesse pedido.
        orders={"ML3": _order(5003)},
        returns={"5003": _ret(9003, "shipped")},
        shipments={"9003": {"status": "shipped", "tracking_number": "OK3BR"}},
    ))
    rows = await _seed(db, [
        _row("B1", "ML1", conta="sem-integracao"),
        _row("B2", "ML2"),
        _row("B3", "ML3"),
    ])

    out = await logistica_meli.returns_por_pedido(db, rows)

    assert set(out) == {"B3"}
    assert out["B3"].tracking == "OK3BR"
    # Conta sem integração nem chega na API.
    assert "ML1" not in fake.fetched("order")


@pytest.mark.asyncio
async def test_shipment_falhando_fica_o_status_do_payload(db, patch_ml):
    # get_shipment 404 => ainda devolve o caso (status do payload de returns,
    # tracking None) em vez de sumir com a devolução.
    patch_ml(FakeML(
        orders={"ML1": _order(5001)},
        returns={"5001": _ret(9001, "ready_to_ship")},
        shipments={},
    ))
    rows = await _seed(db, [_row("B1", "ML1")])

    out = await logistica_meli.returns_por_pedido(db, rows)

    assert out["B1"].status == "ready_to_ship"
    assert out["B1"].tracking is None
    assert out["B1"].return_id == "5001"


@pytest.mark.asyncio
async def test_pedido_sem_devolucao_fica_fora(db, patch_ml):
    fake = patch_ml(FakeML(
        orders={
            "ML1": _order(),  # sem mediação
            "ML2": _order(5002),  # claim sem return (404 no /returns)
        },
        returns={},
    ))
    rows = await _seed(db, [_row("B1", "ML1"), _row("B2", "ML2")])

    out = await logistica_meli.returns_por_pedido(db, rows)

    assert out == {}
    assert fake.fetched("returns") == ["5002"]
    assert fake.fetched("shipment") == []


@pytest.mark.asyncio
async def test_devolucao_aberta_sem_envio_ainda(db, patch_ml):
    # Return existe mas sem `shipments` (comprador ainda não postou): entra
    # com o status do return e tracking None.
    fake = patch_ml(FakeML(
        orders={"ML1": _order(5001)},
        returns={
            "5001": {"id": 9, "status": "opened", "date_created": "2026-08-30T09:00:00.000-03:00"}
        },
    ))
    rows = await _seed(db, [_row("B1", "ML1")])

    out = await logistica_meli.returns_por_pedido(db, rows)

    assert out["B1"] == ReturnInfo(
        fonte="ml", status="opened", tracking=None, carrier=None,
        created_at=datetime(2026, 8, 30, 12, 0, tzinfo=UTC), updated_at=None, return_id="5001",
    )
    assert fake.fetched("shipment") == []


@pytest.mark.asyncio
async def test_created_at_cai_na_data_do_claim(db, patch_ml):
    # Return sem date_created => busca o claim e usa o date_created dele.
    fake = patch_ml(FakeML(
        orders={"ML1": _order(5001)},
        claims={"5001": {"id": 5001, "date_created": "2026-08-18T08:00:00.000-03:00"}},
        returns={"5001": {"id": 1, "shipments": [{"shipment_id": 9001, "status": "shipped"}]}},
        shipments={"9001": {
            "status": "shipped", "tracking_number": "TK000111",
            "last_updated": "2026-08-19T08:00:00.000-03:00",
        }},
    ))
    rows = await _seed(db, [_row("B1", "ML1")])

    out = await logistica_meli.returns_por_pedido(db, rows)

    assert out["B1"].created_at == datetime(2026, 8, 18, 11, 0, tzinfo=UTC)
    assert out["B1"].updated_at == datetime(2026, 8, 19, 11, 0, tzinfo=UTC)
    assert out["B1"].tracking == "TK000111"
    assert fake.fetched("claim") == ["5001"]


@pytest.mark.asyncio
async def test_pack_id_procura_a_devolucao_em_todos_os_pedidos_do_pack(db, patch_ml):
    # O número guardado é um PACK: /orders/{pack} 404 → /packs → cada order.
    # A devolução está no SEGUNDO pedido do pack.
    patch_ml(FakeML(
        orders={"O1": _order(), "O2": _order(5002)},
        packs={"P1": {"id": "P1", "orders": [{"id": "O1"}, {"id": "O2"}]}},
        returns={"5002": _ret(9002, "shipped")},
        shipments={"9002": {"status": "shipped", "tracking_number": "PACK2BR"}},
    ))
    rows = await _seed(db, [_row("B1", "P1")])

    out = await logistica_meli.returns_por_pedido(db, rows)

    assert out["B1"].tracking == "PACK2BR"
    assert out["B1"].return_id == "5002"


@pytest.mark.asyncio
async def test_ignora_linhas_de_outra_plataforma_e_incompletas(db, patch_ml):
    fake = patch_ml(FakeML(
        orders={"ML1": _order(5001)},
        returns={"5001": _ret(9001, "shipped")},
        shipments={"9001": {"status": "shipped", "tracking_number": "X1BR"}},
    ))
    rows = await _seed(db, [
        _row("S1", "SHP1", plataforma="Shopee"),
        _row("B2", None),  # sem pedido do marketplace
        _row("", "ML1"),  # sem pedido bling: não tem chave pra devolver
    ])

    out = await logistica_meli.returns_por_pedido(db, rows)

    assert out == {}
    assert fake.calls == []


@pytest.mark.asyncio
async def test_mesmo_pedido_em_duas_linhas_consulta_uma_vez(db, patch_ml):
    fake = patch_ml(FakeML(
        orders={"ML1": _order(5001)},
        returns={"5001": _ret(9001, "shipped")},
        shipments={"9001": {"status": "shipped", "tracking_number": "DUP1BR"}},
    ))
    rows = await _seed(db, [_row("B1", "ML1"), _row("B2", "ML1 ")])

    out = await logistica_meli.returns_por_pedido(db, rows)

    assert set(out) == {"B1", "B2"}
    assert out["B1"] == out["B2"]
    assert fake.fetched("order") == ["ML1"]


def test_mediation_ids_preserva_o_comportamento_do_enrichment():
    # Helper extraído do build_enrichment: primeiro id truthy, aceita item cru.
    order = {"mediations": [{"id": 0}, {"id": 7}, 8, {"id": 7}]}
    assert logistica_meli._mediation_ids(order) == [7, 8]
    assert logistica_meli._mediation_ids({"mediations": []}) == []
    assert logistica_meli._mediation_ids({}) == []
