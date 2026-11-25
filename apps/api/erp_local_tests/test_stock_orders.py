"""Each stock exit the ERP copies from Bling carries the order behind it (Bling's "V 310306")."""

import json
from decimal import Decimal

from fastapi.testclient import TestClient

from app.erp_local import bling_mirror
from app.erp_local.api import create_app


def link(erp, sku, source):
    with erp.connection() as db:
        db.execute(
            "INSERT OR REPLACE INTO catalog_origin VALUES (?,?)",
            (
                sku,
                json.dumps(
                    {"bling_product_id": source, "catalog_state": "ready", "sellable": True}
                ),
            ),
        )


def order(store_number, number, situation, sku="demo.aparelho", quantity="1"):
    return {
        "store_number": store_number,
        "number": number,
        "situation": situation,
        "items": [{"sku": sku, "quantity": quantity}],
    }


def round_(erp, available, reserved, orders):
    stock = [{"id": "100", "available": str(available), "reserved": str(reserved)}]
    stock[0]["updated_at"] = f"{available}-{reserved}-{len(orders)}-" + ",".join(
        o["situation"] for o in orders
    )
    return bling_mirror.run(erp, lambda sql: stock if sql == bling_mirror.STOCK_SQL else orders)


def last_id(erp):
    with erp.connection() as db:
        return db.execute("SELECT coalesce(max(id),0) FROM movements").fetchone()[0]


def moves(erp, after=0, sku="demo.aparelho"):
    """(physical change, orders) of the mirror's movements after the opening round."""
    with erp.connection() as db:
        rows = db.execute(
            "SELECT id,physical_delta FROM movements WHERE sku=? AND reason=? AND id>? ORDER BY id",
            (sku, bling_mirror.REASON, after),
        ).fetchall()
        links = db.execute("SELECT movement_id,number,quantity FROM movement_orders").fetchall()
    out = []
    for row in rows:
        numbers = sorted(r["number"] for r in links if r["movement_id"] == row["id"])
        out.append((Decimal(row["physical_delta"]), numbers))
    return out


def exits(erp, after):
    return [(delta, numbers) for delta, numbers in moves(erp, after) if delta < 0]


def test_each_exit_gets_the_orders_that_left_with_it_and_reservations_get_none(erp):
    link(erp, "demo.aparelho", "100")
    waiting = [
        order("A1", "310204", "21"),
        order("A2", "310306", "21"),
        order("A3", "310181", "21"),
    ]
    round_(erp, 7, 3, waiting)  # opening: 10 physical, 3 reserved
    start = last_id(erp)
    shipped_one = [order("A1", "310204", "15"), *waiting[1:]]
    assert round_(erp, 7, 2, shipped_one)["pedidos"]["pairs"] == 1
    shipped_all = [
        order("A1", "310204", "15"),
        order("A2", "310306", "15"),
        order("A3", "310181", "15"),
    ]
    round_(erp, 7, 0, shipped_all)
    # A new order only reserves: a movement with no physical change and no order.
    round_(erp, 6, 1, [*shipped_all, order("A4", "310400", "6")])
    assert exits(erp, start) == [(-1, ["310204"]), (-2, ["310181", "310306"])]
    assert all(numbers == [] for delta, numbers in moves(erp, start) if delta >= 0)


def test_an_exit_seen_a_round_before_its_order_is_paired_when_the_order_arrives(erp):
    link(erp, "demo.aparelho", "100")
    waiting = [order("B1", "320001", "21")]
    round_(erp, 4, 1, waiting)
    start = last_id(erp)
    round_(erp, 4, 0, waiting)  # Bling's balance arrived first
    assert exits(erp, start) == [(-1, [])]
    round_(erp, 4, 0, [order("B1", "320001", "15")])
    assert exits(erp, start) == [(-1, ["320001"])]


def test_a_kit_sold_pairs_the_exit_of_each_part(erp):
    link(erp, "demo.aparelho", "100")
    with erp.connection() as db:
        kit = next(
            r["sku"]
            for r in db.execute("SELECT sku,components FROM products WHERE kind='kit'")
            if "demo.aparelho" in r["components"]
        )
        per_kit = next(
            p["quantity"]
            for p in json.loads(
                db.execute("SELECT components FROM products WHERE sku=?", (kit,)).fetchone()[0]
            )
            if p["sku"] == "demo.aparelho"
        )
    units = int(float(per_kit))
    round_(erp, 10, units, [order("C1", "330001", "21", sku=kit)])
    start = last_id(erp)
    round_(erp, 10, 0, [order("C1", "330001", "15", sku=kit)])
    assert exits(erp, start) == [(-units, ["330001"])]


def test_the_stock_route_lists_the_orders_of_each_exit(tmp_path):
    app = create_app(tmp_path / "erp.sqlite3")
    erp = app.state.erp
    erp.command("seed", "demo.seed", {})
    link(erp, "demo.aparelho", "100")
    round_(erp, 7, 2, [order("A1", "310204", "21"), order("A2", "310306", "21")])
    round_(erp, 7, 0, [order("A1", "310204", "15"), order("A2", "310306", "15")])
    client = TestClient(app, base_url="http://localhost")
    items = client.get("/api/erp-local/stock-movements?sku=demo.aparelho").json()["items"]
    exit_item = next(i for i in items if Decimal(i["physical_delta"]) == -2)
    assert sorted(o["number"] for o in exit_item["orders"]) == ["310204", "310306"]
    assert all(i["orders"] == [] for i in items if i is not exit_item)
