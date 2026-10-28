"""Carrinho (pack) do ML com mais de um pacote — a linha da Logística conta o
pacote que pede atenção, não o 1º pedido do pack.

Caso real (Vinicius, 28/10, 304770, poofy): venda 2000015584560777 com 2
malas que o ML mandou em 2 envios. O pacote do envio 48287849457 voltou pra
loja em 27/10 ("Devolução para revisar até sexta-feira") e o outro foi
entregue em Jales/SP em 21/10. O enrich lia só `pack.orders[0]` — o entregue
— e a linha ficou "Pago | Entregue", sem reclamação nem devolução."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Logistica
from app.services import logistica_meli

PACK = "2000015584560777"


class _Resp:
    status_code = 404

    def json(self):
        return {}


class FakePackML:
    """Client ML com pack de verdade: cada pedido, envio, reclamação e
    devolução por id (id ausente => a chamada levanta, como o 404 do ML)."""

    def __init__(self, orders, shipments, claims=None, returns=None, pack_id=PACK):
        self._pack_id = pack_id
        self._ordem = [str(o["id"]) for o in orders]
        self._orders = {str(o["id"]): o for o in orders}
        self._shipments = shipments
        self._claims = claims or {}
        self._returns = returns or {}
        self.envios_lidos: list[str] = []

    async def get_order(self, order_id):
        if str(order_id) not in self._orders:
            raise RuntimeError("Order do not exists")
        return self._orders[str(order_id)]

    async def get_pack(self, pack_id):
        if str(pack_id) != self._pack_id:
            raise RuntimeError("no pack")
        return {"id": self._pack_id, "orders": [{"id": int(i)} for i in self._ordem]}

    async def get_shipment(self, shipment_id):
        self.envios_lidos.append(str(shipment_id))
        if str(shipment_id) not in self._shipments:
            raise RuntimeError("no shipment")
        return self._shipments[str(shipment_id)]

    async def get_claim(self, claim_id):
        if claim_id not in self._claims:
            raise RuntimeError("Claim not found")
        return self._claims[claim_id]

    async def get_claim_returns(self, claim_id):
        if claim_id not in self._returns:
            raise RuntimeError("no returns")
        return self._returns[claim_id]

    async def _request(self, method, path, **kwargs):
        return _Resp()


def _pedido(oid, envio, *, status="paid", mediacoes=()):
    return {
        "id": oid,
        "status": status,
        "shipping": {"id": envio},
        "mediations": [{"id": m} for m in mediacoes],
    }


def _envio(status, tracking, cidade="Jales", uf="SP", substatus=None):
    sh = {
        "status": status,
        "tracking_number": tracking,
        "receiver_address": {"city": {"name": cidade}, "state": {"id": f"BR-{uf}"}},
    }
    if substatus:
        sh["substatus"] = substatus
    return sh


_DEVOLUCAO_NA_LOJA = {
    "shipments": [
        {
            "type": "return",
            "status": "delivered",
            "destination": {
                "name": "seller_address",
                "shipping_address": {"city": {"name": "Piracicaba"}, "state": {"id": "BR-SP"}},
            },
        }
    ]
}


def _caso_304770():
    # O 1º pedido do pack é o pacote ENTREGUE — o que se lia antes.
    return FakePackML(
        orders=[
            _pedido(2000013000000001, 48287849999),
            _pedido(2000013000000002, 48287849457, mediacoes=[5590000001]),
        ],
        shipments={
            "48287849999": _envio("delivered", "SYRGISEPEBI67HBHDPZUZGQLL4"),
            "48287849457": _envio("delivered", "MEL48287849457FMDOR01"),
        },
        claims={5590000001: {"stage": "claim", "status": "opened"}},
        returns={5590000001: _DEVOLUCAO_NA_LOJA},
    )


@pytest.mark.asyncio
async def test_pack_com_dois_envios_mostra_o_pacote_devolvido():
    enr = await logistica_meli.build_enrichment(_caso_304770(), PACK)
    meli = enr["meli_status"]
    assert meli["claim_status"] == "opened"
    assert meli["return_status"] == "delivered"
    assert enr["localizacao_devolucao"] is True
    assert enr["localizacao"] == (
        "Devolvido → loja (Piracicaba/SP) · envio 48287849457 (venda com 2 pacotes)"
    )
    # O rastreio é o do pacote que a linha conta.
    assert enr["rastreio"] == "MEL48287849457FMDOR01"


@pytest.mark.asyncio
async def test_pack_com_um_envio_so_continua_igual_e_le_o_envio_uma_vez():
    # O comum: carrinho num envio só. Nada de "venda com N pacotes" e o envio
    # compartilhado não é lido duas vezes.
    client = FakePackML(
        orders=[_pedido(1, 777), _pedido(2, 777)],
        shipments={"777": _envio("delivered", "SYRG777")},
    )
    enr = await logistica_meli.build_enrichment(client, PACK)
    assert enr["meli_status"] == {"order_status": "paid", "ship_status": "delivered"}
    assert enr["localizacao"] == "Entregue → Jales/SP"
    assert client.envios_lidos == ["777"]


@pytest.mark.asyncio
async def test_pack_num_envio_so_com_reclamacao_no_2o_pedido():
    # Mesmo envio, reclamação aberta no 2º pedido: antes ficava invisível (a
    # varredura via a reclamação, mas o enrich relia o 1º pedido).
    client = FakePackML(
        orders=[_pedido(1, 777), _pedido(2, 777, mediacoes=[55])],
        shipments={"777": _envio("delivered", "SYRG777")},
        claims={55: {"stage": "claim", "status": "opened"}},
    )
    enr = await logistica_meli.build_enrichment(client, PACK)
    assert enr["meli_status"]["claim_status"] == "opened"
    assert "pacotes" not in enr["localizacao"]


@pytest.mark.asyncio
async def test_pack_envio_que_voltou_sem_reclamacao_ganha_do_entregue():
    # Correios não entregou um dos pacotes e ele voltou pelo envio de ida.
    client = FakePackML(
        orders=[_pedido(1, 10), _pedido(2, 20)],
        shipments={
            "10": _envio("delivered", "AA111111111BR"),
            "20": _envio("not_delivered", "AA222222222BR", substatus="returned"),
        },
    )
    enr = await logistica_meli.build_enrichment(client, PACK)
    assert enr["meli_status"]["ship_status"] == "not_delivered"
    assert enr["rastreio"] == "AA222222222BR"
    assert enr["localizacao"].endswith(" · envio 20 (venda com 2 pacotes)")


@pytest.mark.asyncio
async def test_pack_a_caminho_ganha_do_entregue():
    # Venda só termina quando o último pacote chega.
    client = FakePackML(
        orders=[_pedido(1, 10), _pedido(2, 20)],
        shipments={
            "10": _envio("delivered", "SYRG10"),
            "20": _envio("shipped", "SYRG20", cidade="Bauru"),
        },
    )
    enr = await logistica_meli.build_enrichment(client, PACK)
    assert enr["meli_status"]["ship_status"] == "shipped"
    assert enr["rastreio"] == "SYRG20"


@pytest.mark.asyncio
async def test_pack_item_cancelado_nao_puxa_a_linha_pra_cancelado():
    # Um item cancelado antes de sair não pode virar "Cancelado" na linha (a
    # regra da aba Status mexeria no Bling do pedido inteiro) enquanto o outro
    # pacote segue com o comprador.
    client = FakePackML(
        orders=[_pedido(1, 10, status="cancelled"), _pedido(2, 20)],
        shipments={
            "10": _envio("cancelled", ""),
            "20": _envio("shipped", "SYRG20"),
        },
    )
    enr = await logistica_meli.build_enrichment(client, PACK)
    assert enr["meli_status"]["order_status"] == "paid"
    assert enr["meli_status"]["ship_status"] == "shipped"


@pytest.mark.asyncio
async def test_pack_todo_cancelado_continua_cancelado():
    client = FakePackML(
        orders=[_pedido(1, 10, status="cancelled"), _pedido(2, 10, status="cancelled")],
        shipments={"10": _envio("cancelled", "")},
    )
    enr = await logistica_meli.build_enrichment(client, PACK)
    assert enr["meli_status"]["order_status"] == "cancelled"


@pytest.mark.asyncio
async def test_pack_sem_nenhum_pedido_legivel_levanta():
    # O pack existe e lista os pedidos, mas nenhum responde: erro, não linha vazia.
    client = FakePackML(orders=[_pedido(1, 10), _pedido(2, 20)], shipments={})
    client._orders.clear()
    with pytest.raises(RuntimeError, match="nenhum pedido"):
        await logistica_meli.build_enrichment(client, PACK)


@pytest.mark.asyncio
async def test_sweep_pack_com_pacote_entregue_nao_marca_toda_rodada(
    db: AsyncSession, make_user, monkeypatch
):
    """A linha do pack mostra o pacote que pede atenção (aqui o que ainda está
    a caminho); o outro pedido do pack segue com a tag "delivered". Esse
    descompasso é legítimo: só marca de novo se o pedido mexeu DEPOIS da
    última leitura."""
    from app.models import Integration, IntegrationPlatform
    from app.security.cipher import encrypt_json

    dono = await make_user(email="sweep-ml-pack@davinci-test.com")
    integ = Integration(
        platform=IntegrationPlatform.ML, name="conta-pack", status="active",
        credentials=encrypt_json({"access_token": "x", "user_id": "1"}),
        user_id=dono.id,
    )
    db.add(integ)

    agora = datetime.now(UTC)

    def _linha(numero: str, pack: str) -> Logistica:
        return Logistica(
            pedido_bling=numero, pedido_marketplace=pack, plataforma="Mercado Livre",
            conta="conta-pack", data=agora.date(),
            meli_status={"order_status": "paid", "ship_status": "shipped"},
            status_lido_em=agora - timedelta(hours=1),
        )

    ja_lido = _linha("9201", "PACKA")  # o entregue mexeu antes da leitura
    mexeu_depois = _linha("9202", "PACKB")  # o entregue mexeu depois
    for linha in (ja_lido, mexeu_depois):
        db.add(linha)
    await db.commit()

    def _iso(d: datetime) -> str:
        return d.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000-00:00")

    class _FakeClient:
        async def search_orders_updated(self, *, seller_id, date_from, date_to, limit, offset):
            if offset:
                return {"results": [], "paging": {"total": 2}}
            return {"results": [
                {"id": "5001", "pack_id": "PACKA", "status": "paid", "tags": ["delivered"],
                 "date_last_updated": _iso(agora - timedelta(hours=3))},
                {"id": "5002", "pack_id": "PACKB", "status": "paid", "tags": ["delivered"],
                 "date_last_updated": _iso(agora - timedelta(minutes=5))},
            ], "paging": {"total": 2}}

        async def buscar_reclamacoes(self, *, status, offset, limit, sort=None):
            return {"data": [], "paging": {"total": 0}}

    async def _integ(session, conta):
        return integ

    monkeypatch.setattr(logistica_meli, "_ml_integration_for_conta", _integ)
    monkeypatch.setattr(logistica_meli, "_build_ml_client", lambda session, integ: _FakeClient())
    monkeypatch.setattr(
        logistica_meli, "decrypt_json", lambda _c: {"access_token": "x", "user_id": "1"}
    )

    out = await logistica_meli.sweep_pos_venda(db)
    marcados = {str(i) for i in out["ids"]}
    assert str(ja_lido.id) not in marcados
    assert str(mexeu_depois.id) in marcados
