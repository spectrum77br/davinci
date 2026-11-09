"""Turnover steps with synthetic orders: ids kept, replays safe, differences listed."""

from decimal import Decimal

from test_core import run
from test_product_details import with_origin

from app.erp_local import cutover, readiness
from app.erp_local.bling_compat import local_id


def raw_order(identifier=555, situation=6, sku="demo.aparelho", **extra):
    return {
        "id": identifier,
        "numero": 1000,
        "numeroLoja": f"LOJA-{identifier}",
        "data": "2026-11-06",
        "situacao": {"id": situation},
        "loja": {"id": 0},
        "contato": {"nome": "Comprador", "numeroDocumento": "529.982.247-25", "tipoPessoa": "F"},
        "itens": [{"codigo": sku, "quantidade": 2, "valor": 50}],
        "taxas": {"valorBase": 90, "custoFrete": 12.5, "taxaComissao": 15},
        "numeroPedidoCompra": "PC-1",
        "transporte": {
            "etiqueta": {
                "nome": "Quem Recebe",
                "endereco": "Rua Entrega",
                "numero": "10",
                "bairro": "Centro",
                "municipio": "Belo Horizonte",
                "uf": "MG",
                "cep": "30110-000",
            }
        },
        **extra,
    }


def physical(erp, sku="demo.aparelho"):
    with erp.connection() as db:
        return erp.balance(db, sku, "geral")


def test_city_lookup_uses_official_codes_and_ignores_accents():
    assert cutover.city_code("São Paulo", "SP") == "3550308"
    assert cutover.city_code("SAO PAULO", "SP") == "3550308"
    assert cutover.city_code("Belo Horizonte", "MG") == "3106200"
    assert cutover.city_code("Cidade Inventada", "SP") == ""
    assert cutover.city_code("São Paulo", "MG") == ""


def test_open_orders_keep_their_ids_and_reserve_once(erp):
    before = physical(erp)
    report = cutover.import_open_orders(erp, [raw_order()], depot="geral")
    assert report["imported"] == 1 and report["blocked"] == []
    assert physical(erp)[1] == before[1] + Decimal(2)
    with erp.connection() as db:
        identifier = local_id(db, "order", 555)
    order = next(o for o in erp.snapshot()["orders"] if o["id"] == identifier)
    assert order["data"]["contact"]["document"] == "52998224725"
    assert order["data"]["contact"]["ibge_code"] == "3106200"
    assert order["data"]["recipient"]["name"] == "Quem Recebe"
    assert order["data"]["channel_fees"]["shipping_cost"] == "12.5"
    again = cutover.import_open_orders(erp, [raw_order()], depot="geral")
    assert again["already"] == 1 and physical(erp)[1] == before[1] + Decimal(2)


def test_closed_orders_stay_out_and_unknown_products_are_listed(erp):
    before = physical(erp)
    report = cutover.import_open_orders(
        erp,
        [raw_order(1, situation=9), raw_order(2, situation=12), raw_order(3, sku="nao.existe")],
        depot="geral",
    )
    assert report["closed"] == 2 and report["imported"] == 0
    assert [b["id"] for b in report["blocked"]] == [3]
    assert physical(erp) == before


def test_opening_balances_reach_the_reported_physical_and_replay_safely(erp):
    with_origin(erp, "demo.aparelho", 1001)
    rows = [
        {"produto": {"id": 1001}, "saldoFisicoTotal": 25, "saldoVirtualTotal": 25},
        {"produto": {"id": 9999}, "saldoFisicoTotal": 3, "saldoVirtualTotal": 3},
    ]
    report = cutover.apply_opening_balances(erp, rows, depot="geral")
    assert report["adjusted"] == 1 and report["unknown"] == 1
    assert physical(erp)[0] == Decimal(25)
    assert cutover.apply_opening_balances(erp, rows, depot="geral")["same"] == 1


def test_balance_comparison_lists_every_reservation_disagreement(erp):
    with_origin(erp, "demo.aparelho", 1001)
    run(
        erp,
        "stock.post",
        {"sku": "demo.aparelho", "depot": "geral", "type": "B", "quantity": "10", "reason": "x"},
    )
    same = [{"produto": {"id": 1001}, "saldoVirtualTotal": 10}]
    assert cutover.compare_balances(erp, same, depot="geral")["differences"] == []
    cutover.import_open_orders(erp, [raw_order()], depot="geral")
    report = cutover.compare_balances(erp, same, depot="geral")
    assert report["differences"] == [{"sku": "demo.aparelho", "here": "8.000", "bling": "10"}]


def test_readiness_never_declares_ready_on_local_evidence(erp):
    with erp.connection() as db:
        result = readiness.report(db)
    assert result["ready_to_replace"] is False
    assert {"nfe_value_rule", "recipient_shopee", "recipient_amazon", "nfe_homologation"} <= set(
        result["needs_owner"]
    )
    assert all(g["state"] != "ok" for g in result["gates"] if g["id"] in {"labels", "parallel_run"})
