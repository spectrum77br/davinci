"""Lot decisions against the rules written in DaVinci's stock priority robot."""

from itertools import count

import pytest
from test_core import run

from app.erp_local import stock_priority
from app.erp_local.core import ERPError


def stocked(erp, sku, quantity):
    run(erp, "product.create", {"sku": sku, "name": sku, "kind": "simple"})
    if quantity:
        run(
            erp,
            "stock.post",
            {"sku": sku, "depot": "geral", "type": "E", "quantity": str(quantity), "reason": "x"},
        )


def kit(erp, sku, parts):
    run(
        erp,
        "product.create",
        {
            "sku": sku,
            "name": sku,
            "kind": "kit",
            "components": [{"sku": part, "quantity": "1"} for part in parts],
        },
    )


keys = count(1)


def sell(erp, *skus, flex=False, quantity="1"):
    return run(
        erp,
        "order.import",
        {
            "channel": "Teste",
            "store": "A",
            "external_id": f"P{next(keys)}",
            "depot": "geral",
            "items": [{"sku": s, "quantity": quantity, "unit_price": "10"} for s in skus],
            **({"fulfillment_mode": "flex"} if flex else {}),
        },
    )["id"]


def rule(erp, base, target):
    run(erp, "stock.priority.rule", {"base": base, "lot": target})


def go(erp, identifier):
    return run(erp, "order.stock_priority", {"id": identifier})


def lots_of(erp, identifier):
    data = next(o for o in erp.snapshot()["orders"] if o["id"] == identifier)["data"]
    return [i["sku"] for i in data.get("fulfillment_items", data["items"])]


def commercial_of(erp, identifier):
    data = next(o for o in erp.snapshot()["orders"] if o["id"] == identifier)["data"]
    return [(i["sku"], i["unit_price"], i["quantity"]) for i in data["items"]]


def balance(erp, sku):
    product = next(p for p in erp.snapshot()["products"] if p["sku"] == sku)
    return next(b for b in product["balances"] if b["depot"] == "geral")


def test_priority_moves_a_whole_kit_and_keeps_the_commercial_item(erp):
    for sku in ["a.ci", "f.ci", "a.sp", "f.sp"]:
        stocked(erp, sku, 3)
    kit(erp, "a.ci+f.ci", ["a.ci", "f.ci"])
    kit(erp, "a.sp+f.sp", ["a.sp", "f.sp"])
    rule(erp, "a", "sp")
    order = sell(erp, "a.ci+f.ci")
    assert float(balance(erp, "a.ci")["reserved"]) == 1
    result = go(erp, order)
    assert result["decision"] == "move" and result["reason"] == "prioridade"
    assert lots_of(erp, order) == ["a.sp+f.sp"]
    assert commercial_of(erp, order)[0][0] == "a.ci+f.ci"
    assert float(balance(erp, "a.ci")["reserved"]) == 0
    assert float(balance(erp, "a.sp")["reserved"]) == 1
    assert go(erp, order)["decision"] == "keep"


def test_priority_without_target_stock_or_product_changes_nothing(erp):
    stocked(erp, "a.ci", 3)
    stocked(erp, "a.sp", 0)
    stocked(erp, "b.ci", 3)
    rule(erp, "a", "sp")
    rule(erp, "b", "sp")
    first, second = sell(erp, "a.ci"), sell(erp, "b.ci")
    assert go(erp, first)["decision"] == "keep" and lots_of(erp, first) == ["a.ci"]
    assert go(erp, second)["decision"] == "keep" and lots_of(erp, second) == ["b.ci"]


def test_order_stays_in_one_lot_when_one_lot_covers_everything(erp):
    for sku in ["a.ci", "b.ci", "a.sp", "b.sp"]:
        stocked(erp, sku, 2)
    rule(erp, "a", "sp")
    order = sell(erp, "a.ci", "b.ci")
    result = go(erp, order)
    assert result["reason"] == "estoque_unico_prioridade"
    assert lots_of(erp, order) == ["a.sp", "b.sp"]


def test_mixed_order_gathers_in_the_lot_with_more_stock_when_no_priority(erp):
    stocked(erp, "a.ci", 2)
    stocked(erp, "b.ci", 5)
    stocked(erp, "a.sp", 9)
    stocked(erp, "b.sp", 9)
    order = sell(erp, "a.ci", "b.sp")
    assert go(erp, order)["reason"] == "estoque_unico_mais_saldo"
    assert lots_of(erp, order) == ["a.sp", "b.sp"]


def test_no_single_lot_covers_so_each_item_stays_or_follows_its_own_priority(erp):
    stocked(erp, "a.ci", 2)
    stocked(erp, "b.ci", 0)
    stocked(erp, "a.sp", 0)
    stocked(erp, "b.sp", 2)
    order = sell(erp, "a.ci", "b.sp")
    assert go(erp, order)["decision"] == "keep" and lots_of(erp, order) == ["a.ci", "b.sp"]


def test_flex_is_mandatory_and_runs_out_to_a_person_not_to_another_lot(erp):
    stocked(erp, "a.ci", 5)
    stocked(erp, "a.sp", 1)
    stocked(erp, "a.pi", 5)
    rule(erp, "a", "pi")
    served = sell(erp, "a.ci", flex=True)
    result = go(erp, served)
    assert result["reason"] == "flex" and lots_of(erp, served) == ["a.sp"]
    lacking = sell(erp, "a.ci", flex=True)
    result = go(erp, lacking)
    assert result["decision"] == "alert" and result["alert"] is True
    assert lots_of(erp, lacking) == ["a.ci"]


def test_units_promised_to_flex_are_held_back_from_normal_orders(erp):
    stocked(erp, "a.ci", 5)
    stocked(erp, "a.sp", 1)
    rule(erp, "a", "sp")
    sell(erp, "a.ci", flex=True)
    normal = sell(erp, "a.ci")
    assert go(erp, normal)["decision"] == "keep" and lots_of(erp, normal) == ["a.ci"]


def test_sweep_serves_flex_before_older_normal_orders(erp):
    stocked(erp, "a.ci", 5)
    stocked(erp, "a.sp", 1)
    rule(erp, "a", "sp")
    normal = sell(erp, "a.ci")
    flex = sell(erp, "a.ci", flex=True)
    numbers = count(1)
    report = stock_priority.sweep(erp, new_key=lambda: f"sweep-{next(numbers)}")
    assert report["moved"] == 1 and report["alerts"] == []
    assert lots_of(erp, flex) == ["a.sp"] and lots_of(erp, normal) == ["a.ci"]


def test_robot_does_not_flip_a_swap_it_already_made(erp):
    stocked(erp, "a.ci", 5)
    stocked(erp, "a.sp", 5)
    rule(erp, "a", "sp")
    order = sell(erp, "a.ci")
    go(erp, order)
    rule(erp, "a", "ci")
    result = go(erp, order)
    assert result["decision"] == "keep" and result["reason"] == "trava_troca_repetida"
    assert lots_of(erp, order) == ["a.sp"]


def test_mixed_or_unlotted_sku_and_shipped_orders_are_left_alone(erp):
    stocked(erp, "sem.lote", 3)
    order = sell(erp, "sem.lote")
    assert go(erp, order)["decision"] == "keep"
    run(erp, "logistics.prepare", {"id": order, "service": "Teste"})
    with pytest.raises(ERPError, match="separação"):
        go(erp, order)


def test_rule_validation(erp):
    with pytest.raises(ERPError, match="estoque de venda"):
        rule(erp, "a", "cd")
    rule(erp, "A", "sp")
    with erp.connection() as db:
        assert stock_priority.snapshot(db)[0]["base"] == "a"
    rule(erp, "a", None)
    with erp.connection() as db:
        assert stock_priority.snapshot(db) == []
