"""Pack do ML consolidado num pedido Bling: frete e cupom dos irmãos entram.

Bug (2026-09-19, pedido Bling 298130 / pack 2000015104547867): o pack tem 2
sub-pedidos (2000018535437580 sku b021.24 R$278,81 e 2000018535444032 sku
b001.24 R$286,26) num único envio 48050459517 que custou R$135,75. A
expansão de pack em `_fetch_ml` já somava bruto e comissão dos dois, MAS:

* o frete era rateado pela participação SÓ do pedido primário no envio
  (peso x qty → 0,5 → 67,88) — metade do que o ML cobrou;
* o cupom do vendedor vinha só de `/orders/{primário}/discounts` (2,97),
  deixando de fora o cupom do irmão (3,03).

Resultado: líquido 429,48 (Margem 65,2%) contra os 358,58 da tela do ML.
Agora o rateio usa a participação do CONJUNTO casado e o cupom soma todos
os sub-pedidos com `order_has_discount`; as linhas de frete cobrem os itens
de todos eles (o dedup por shipping_id em `_ml_actual_freight_total` segue
contando o envio uma vez só).
"""
from __future__ import annotations

from decimal import Decimal

from app.services.marketplace_financials import (
    _fetch_ml,
    _ml_actual_freight_total,
    _ml_order_freight_share,
)

PACK = "2000015104547867"
PRIMARIO = "2000018535437580"
IRMAO = "2000018535444032"
ENVIO = "48050459517"
SELLER = "1593540211"


def _order(order_id: str, item_id: str, sku: str, price: str, fee: str, tags: list[str]) -> dict:
    return {
        "id": int(order_id),
        "pack_id": int(PACK),
        "total_amount": float(price),
        "paid_amount": float(price),
        "currency_id": "BRL",
        "tags": tags,
        "seller": {"id": int(SELLER)},
        "shipping": {"id": int(ENVIO)},
        "order_items": [
            {
                "item": {"id": item_id, "seller_sku": sku, "title": f"Mala {sku}"},
                "quantity": 1,
                "unit_price": float(price),
                "sale_fee": float(fee),
            }
        ],
        "payments": [{"status": "approved", "shipping_cost": 0.0, "coupon_amount": 0.0}],
    }


def _discounts(item_id: str, seller_coupon: str) -> dict:
    return {
        "details": [
            {
                "type": "coupon",
                "items": [{"id": item_id, "amounts": {"total": float(seller_coupon), "seller": float(seller_coupon)}, "quantity": 1}],
                "supplier": {"campaign_id": 1, "meli_campaign": None},
            },
            {
                "type": "discount",
                "items": [{"id": item_id, "amounts": {"total": 19.19, "seller": 18.01}, "quantity": 1}],
                "supplier": {"offer_id": f"OFFER-{item_id}-1", "funding_mode": "sale_fee"},
            },
        ]
    }


SHIPMENT_COSTS = {
    "senders": [{"cost": 135.75, "save": 45.25, "user_id": int(SELLER), "discounts": [{"rate": 0.25, "type": "mandatory", "promoted_amount": 45.25}]}],
    "receiver": {"cost": 0, "user_id": 441706891},
    "gross_amount": 240.9,
}

SHIPMENT_ITEMS = [
    {"item_id": "MLB6953118822", "order_id": PRIMARIO, "quantity": 1, "dimensions": {"width": 35, "height": 23, "length": 52, "weight": 7000}},
    {"item_id": "MLB6953118804", "order_id": IRMAO, "quantity": 1, "dimensions": {"width": 35, "height": 23, "length": 52, "weight": 7000}},
]

FREE_QUOTE = {"coverage": {"all_country": {"discount": {"rate": 0.25, "type": "mandatory", "promoted_amount": 90.5}, "list_cost": 67.88, "currency_id": "BRL", "billable_weight": 7000}}}


class _FakeMLClient:
    """Reproduz as respostas reais do pack 2000015104547867 (19/09/2026)."""

    creds = {"user_id": SELLER}

    def __init__(self, *, sibling_discounts_fail: bool = False) -> None:
        self.orders = {
            PRIMARIO: _order(PRIMARIO, "MLB6953118822", "b021.24", "278.81", "31.92", ["order_has_discount", "paid", "pack_order"]),
            IRMAO: _order(IRMAO, "MLB6953118804", "b001.24", "286.26", "32.82", ["order_has_discount", "paid", "pack_order"]),
        }
        self.discounts = {
            PRIMARIO: _discounts("MLB6953118822", "2.97"),
            IRMAO: _discounts("MLB6953118804", "3.03"),
        }
        self.sibling_discounts_fail = sibling_discounts_fail
        self.calls: list[tuple[str, str]] = []

    async def get_order(self, order_id: str) -> dict:
        self.calls.append(("order", str(order_id)))
        return self.orders[str(order_id)]

    async def get_pack(self, pack_id: str) -> dict:
        self.calls.append(("pack", str(pack_id)))
        assert str(pack_id) == PACK
        return {"id": int(PACK), "orders": [{"id": int(PRIMARIO)}, {"id": int(IRMAO)}]}

    async def get_billing_order_details(self, order_id: str) -> dict:
        return {"results": [{"details": [{"charge_info": {"detail_amount": 24.52}}]}]}

    async def get_order_discounts(self, order_id: str) -> dict:
        self.calls.append(("discounts", str(order_id)))
        if self.sibling_discounts_fail and str(order_id) == IRMAO:
            raise RuntimeError("boom")
        return self.discounts[str(order_id)]

    async def get_shipment_costs(self, shipping_id: str) -> dict:
        assert str(shipping_id) == ENVIO
        return SHIPMENT_COSTS

    async def get_shipment_items(self, shipping_id: str) -> list:
        return SHIPMENT_ITEMS

    async def get_free_shipping_options(self, seller_id, item_id: str) -> dict:
        return FREE_QUOTE


async def test_pack_consolidado_no_bling_soma_frete_e_cupom_dos_irmaos():
    client = _FakeMLClient()
    snap = await _fetch_ml(client, PRIMARIO, bling_skus={"b021.24", "b001.24"})

    assert snap.gross_amount == Decimal("565.07")
    assert snap.fee_amount == Decimal("64.74")
    # Envio inteiro (os dois sub-pedidos casados = 100% do pacote), não a metade.
    assert snap.freight_amount == Decimal("135.75")
    # Cupom do primário + cupom do irmão.
    assert snap.discount_amount == Decimal("6.00")
    # = "Total R$ 358,58" na tela da venda do ML.
    assert snap.net_amount == Decimal("358.58")

    # Uma linha de frete por item de cada sub-pedido, mesmo envio.
    assert [f.sku for f in snap.freights] == ["b021.24", "b001.24"]
    assert {f.shipping_id for f in snap.freights} == {ENVIO}
    assert [f.raw["order_id"] for f in snap.freights] == [PRIMARIO, IRMAO]
    assert all(f.freight_actual_amount == Decimal("135.75") for f in snap.freights)
    assert all(f.freight_promised_amount == Decimal("67.88") for f in snap.freights)
    assert _ml_actual_freight_total(snap.freights) == Decimal("135.75")

    eventos = {e.event_type: e.amount for e in snap.events}
    assert eventos["freight"] == Decimal("-135.75")
    assert eventos["frete_anuncio"] == Decimal("135.76")
    assert eventos["discount"] == Decimal("-6.00")
    assert eventos["net_estimated"] == Decimal("358.58")

    assert snap.raw["pack_matched_order_ids"] == [PRIMARIO, IRMAO]
    assert snap.raw["sibling_order_discounts"] == {IRMAO: client.discounts[IRMAO]}
    assert ("discounts", IRMAO) in client.calls


async def test_pack_que_o_bling_separou_em_dois_pedidos_fica_com_a_fatia_do_primario():
    # Bling criou um pedido por sub-pedido: o irmão não casa por SKU, então este
    # pedido Bling fica só com a própria fatia do envio e o próprio cupom.
    # (Guarda de não-regressão: esses números já eram assim antes da correção.)
    client = _FakeMLClient()
    snap = await _fetch_ml(client, PRIMARIO, bling_skus={"b021.24"})

    assert snap.gross_amount == Decimal("278.81")
    assert snap.fee_amount == Decimal("31.92")
    assert snap.freight_amount == Decimal("67.88")
    assert snap.discount_amount == Decimal("2.97")
    assert snap.net_amount == Decimal("176.04")
    assert [f.sku for f in snap.freights] == ["b021.24"]
    assert snap.raw["pack_matched_order_ids"] == [PRIMARIO]
    assert snap.raw["sibling_order_discounts"] is None
    assert ("discounts", IRMAO) not in client.calls


async def test_cupom_do_irmao_que_falha_nao_derruba_o_resto():
    client = _FakeMLClient(sibling_discounts_fail=True)
    snap = await _fetch_ml(client, PRIMARIO, bling_skus={"b021.24", "b001.24"})

    assert snap.freight_amount == Decimal("135.75")
    assert snap.discount_amount == Decimal("2.97")
    assert snap.net_amount == Decimal("361.61")
    # O motivo fica no raw pra quem for comparar com a tela do ML.
    assert snap.raw["sibling_order_discounts"] == {IRMAO: {"error": "boom"}}


async def test_so_consulta_cupom_de_quem_tem_a_tag():
    # Primário sem cupom, irmão com: só o irmão é consultado e conta.
    client = _FakeMLClient()
    client.orders[PRIMARIO]["tags"] = ["paid", "pack_order"]
    snap = await _fetch_ml(client, PRIMARIO, bling_skus={"b021.24", "b001.24"})

    assert [c for c in client.calls if c[0] == "discounts"] == [("discounts", IRMAO)]
    assert snap.discount_amount == Decimal("3.03")
    assert snap.raw["order_discounts"] is None
    assert snap.raw["sibling_order_discounts"] == {IRMAO: client.discounts[IRMAO]}

    # Ninguém com cupom: desconto continua ausente (sem evento discount).
    client = _FakeMLClient()
    client.orders[PRIMARIO]["tags"] = ["paid", "pack_order"]
    client.orders[IRMAO]["tags"] = ["paid", "pack_order"]
    snap = await _fetch_ml(client, PRIMARIO, bling_skus={"b021.24", "b001.24"})
    assert snap.discount_amount is None
    assert "discount" not in {e.event_type for e in snap.events}
    assert snap.net_amount == Decimal("364.58")


async def test_irmao_com_duas_unidades_entra_no_rateio_e_no_frete_anuncio():
    client = _FakeMLClient()
    client.orders[IRMAO]["order_items"][0]["quantity"] = 2
    client.orders[IRMAO]["total_amount"] = 572.52
    # No envio: primário 1 un, irmão 2 un, e um pedido de OUTRO comprador
    # (carrinho) com 1 un — o conjunto casado fica com 3/4 do custo.
    items = [
        dict(SHIPMENT_ITEMS[0]),
        {**SHIPMENT_ITEMS[1], "quantity": 2},
        {"item_id": "MLB999", "order_id": "2000099999999999", "quantity": 1, "dimensions": {"weight": 7000}},
    ]
    client.get_shipment_items = lambda shipping_id: _async(items)  # type: ignore[method-assign]
    client.get_shipment_costs = lambda shipping_id: _async({"senders": [{"cost": 200.00}]})  # type: ignore[method-assign]
    snap = await _fetch_ml(client, PRIMARIO, bling_skus={"b021.24", "b001.24"})

    assert snap.freight_amount == Decimal("150.00")
    promised = {f.sku: f.freight_promised_amount for f in snap.freights}
    assert promised == {"b021.24": Decimal("67.88"), "b001.24": Decimal("135.76")}
    assert {e.event_type: e.amount for e in snap.events}["frete_anuncio"] == Decimal("203.64")


async def test_irmao_em_outro_envio_fica_fora_das_linhas_de_frete():
    # Não deveria existir (pack do ML = um envio), mas se vier, o irmão não
    # pode ganhar linha com o shipping_id e o custo do envio do primário.
    client = _FakeMLClient()
    client.orders[IRMAO]["shipping"] = {"id": 99999}
    snap = await _fetch_ml(client, PRIMARIO, bling_skus={"b021.24", "b001.24"})

    assert snap.gross_amount == Decimal("565.07")  # bruto segue somando o irmão
    assert [f.sku for f in snap.freights] == ["b021.24"]
    assert snap.freights[0].raw["matched_order_ids"] is None
    assert snap.freights[0].raw["siblings_outro_envio"] == {IRMAO: "99999"}
    # Só o primário está neste envio → fatia dele (0,5).
    assert snap.freight_amount == Decimal("67.88")


async def _async(value):
    return value


def _shipment_item(order_id: str, qty: int = 1, weight: int | None = 7000) -> dict:
    dims = {"weight": weight} if weight is not None else {}
    return {"order_id": order_id, "quantity": qty, "dimensions": dims}


def test_freight_share_soma_os_sub_pedidos_casados():
    payload = [_shipment_item("1"), _shipment_item("2"), _shipment_item("3", qty=2)]
    assert _ml_order_freight_share(payload, "1") == Decimal("0.25")
    assert _ml_order_freight_share(payload, ["1", "2"]) == Decimal("0.5")
    assert _ml_order_freight_share(payload, ["1", "2", "3"]) == Decimal("1")
    # Ids que não estão no envio não contam; lista vazia = não dá pra calcular.
    assert _ml_order_freight_share(payload, ["9"]) is None
    assert _ml_order_freight_share(payload, []) is None
