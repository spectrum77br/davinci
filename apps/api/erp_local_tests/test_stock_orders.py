"""Each stock exit the ERP copies from Bling carries the order behind it (Bling's "V 310306")."""

import json
from datetime import UTC, datetime, timedelta
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


def round_(erp, available, reserved, orders, shipped=()):
    stock = [{"id": "100", "available": str(available), "reserved": str(reserved)}]
    stock[0]["updated_at"] = f"{available}-{reserved}-{len(orders)}-" + ",".join(
        o["situation"] for o in orders
    )

    def reader(sql):
        if sql == bling_mirror.STOCK_SQL:
            return stock
        return list(shipped) if sql == bling_mirror.SHIPPED_SQL else orders

    return bling_mirror.run(erp, reader)


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


def opened(erp):
    """The opening round's movements, two hours back: what the tests look at comes after them."""
    start = last_id(erp)
    with erp.connection() as db:
        for row in db.execute("SELECT id,created_at FROM movements WHERE id<=?", (start,)):
            back = datetime.fromisoformat(row["created_at"]) - timedelta(hours=2)
            db.execute(
                "UPDATE movements SET created_at=? WHERE id=?", (back.isoformat(), row["id"])
            )
        db.commit()
    return start


def exit_rows(erp, after):
    with erp.connection() as db:
        return [
            (r["id"], datetime.fromisoformat(r["created_at"]))
            for r in db.execute(
                "SELECT id,created_at FROM movements WHERE id>? AND reason=? AND "
                "CAST(physical_delta AS REAL)<0 ORDER BY id",
                (after, bling_mirror.REASON),
            )
        ]


def ship(number, moment, sku="demo.aparelho", quantity=1, situation="15"):
    """A sale as DaVinci has it: the moment it entered "em andamento" (bling_envio_evento), its
    situation now and its items."""
    return {
        "number": number,
        "shipped": moment.isoformat(),
        "situation": situation,
        "items": [{"sku": sku, "quantity": quantity}],
    }


def link_with(erp, shipped, now=None):
    return bling_mirror.attribute(erp, lambda _sql: shipped, now=now)


def test_each_exit_gets_the_sales_that_entered_em_andamento_just_before_it(erp):
    link(erp, "demo.aparelho", "100")
    waiting = [
        order("A1", "310204", "21"),
        order("A2", "310306", "21"),
        order("A3", "310181", "21"),
    ]
    round_(erp, 7, 3, waiting)  # opening: 10 physical, 3 reserved
    start = opened(erp)
    shipped_all = [
        order("A1", "310204", "15"),
        order("A2", "310306", "15"),
        order("A3", "310181", "15"),
    ]
    round_(erp, 7, 2, [shipped_all[0], *waiting[1:]])  # one exit
    round_(erp, 7, 0, shipped_all)  # the next two, seen together a round later
    # A new sale only reserves: a movement with no physical change and no sale.
    round_(erp, 6, 1, [*shipped_all, order("A4", "310400", "6")])
    (_, first), _merged = exit_rows(erp, start)
    # 24/11/2026: the three entered 15 in the same minute, a little before the mirror saw them.
    shipped = [
        ship("310204", first - timedelta(seconds=50)),
        ship("310306", first - timedelta(seconds=20)),
        ship("310181", first - timedelta(seconds=5)),
    ]
    assert link_with(erp, shipped)["pairs"] == 3
    assert exits(erp, start) == [(-1, ["310204"]), (-2, ["310181", "310306"])]
    assert all(numbers == [] for delta, numbers in moves(erp, start) if delta >= 0)
    with erp.connection() as db:
        moment = db.execute(
            "SELECT shipped_at FROM movement_orders WHERE number='310204'"
        ).fetchone()[0]
    assert moment == (first - timedelta(seconds=50)).astimezone(UTC).isoformat(timespec="seconds")
    # Run again: nothing is linked twice.
    assert link_with(erp, shipped)["pairs"] == 0


def test_every_round_links_what_it_can_and_a_sale_recorded_after_its_exit_still_links(erp):
    link(erp, "demo.aparelho", "100")
    round_(erp, 4, 1, [order("B1", "320001", "21")])
    start = opened(erp)
    # Bling's balance reached the ERP before DaVinci wrote down the sale's moment.
    round_(erp, 4, 0, [order("B1", "320001", "15")])
    assert exits(erp, start) == [(-1, [])]
    ((_, seen),) = exit_rows(erp, start)
    report = round_(
        erp, 4, 0, [order("B1", "320001", "15")], [ship("320001", seen + timedelta(seconds=40))]
    )
    assert report["pedidos"]["pairs"] == 1
    assert exits(erp, start) == [(-1, ["320001"])]


def test_a_kit_sold_links_the_exit_of_each_part(erp):
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
    start = opened(erp)
    round_(erp, 10, 0, [order("C1", "330001", "15", sku=kit)])
    ((_, seen),) = exit_rows(erp, start)
    link_with(erp, [ship("330001", seen - timedelta(seconds=30), sku=kit)])
    assert exits(erp, start) == [(-units, ["330001"])]


def test_the_first_try_is_redone_and_a_sale_far_from_an_exit_never_links(erp):
    link(erp, "demo.aparelho", "100")
    sales = [order("D1", "309993", "83953"), order("D2", "310288", "15")]
    round_(erp, 9, 0, sales)
    start = opened(erp)
    round_(erp, 8, 0, sales)  # a hand-made adjustment, no sale behind it
    round_(erp, 7, 0, sales)  # 310288's exit
    (hand, _), (sale, seen) = exit_rows(erp, start)
    with erp.connection() as db:
        db.execute(
            "UPDATE movements SET created_at=? WHERE id=?",
            ((seen - timedelta(minutes=10)).isoformat(), hand),
        )
        # 25/11/2026's first try put 309993 (delivered that afternoon) on 310288's exit.
        db.execute(
            "INSERT INTO movement_orders(movement_id,number,quantity,source) "
            "VALUES (?,'309993','1','historico')",
            (sale,),
        )
        db.commit()
    shipped = [
        ship("310288", seen - timedelta(seconds=90)),
        ship("309993", seen - timedelta(days=1), situation="83953"),  # it shipped the day before
        # Left and was cancelled: its units came back, it explains no exit.
        ship("310290", seen - timedelta(seconds=60), situation="12"),
    ]
    assert link_with(erp, shipped)["pairs"] == 1
    assert exits(erp, start) == [(-1, []), (-1, ["310288"])]
    with erp.connection() as db:
        assert [r[0] for r in db.execute("SELECT DISTINCT source FROM movement_orders")] == [
            "envio"
        ]


def test_the_stock_route_lists_each_exit_sales_and_those_holding_the_reservation(tmp_path):
    app = create_app(tmp_path / "erp.sqlite3")
    erp = app.state.erp
    erp.command("seed", "demo.seed", {})
    link(erp, "demo.aparelho", "100")
    round_(erp, 7, 2, [order("A1", "310204", "21"), order("A2", "310306", "21")])
    start = opened(erp)
    holding = [
        order("A1", "310204", "15"),
        order("A2", "310306", "21"),
        order("A5", "310420", "6", quantity="2"),
    ]
    round_(erp, 5, 3, holding)  # 310204 left: 9 physical to 8
    ((_, seen),) = exit_rows(erp, start)
    link_with(erp, [ship("310204", seen - timedelta(seconds=30))])
    client = TestClient(app, base_url="http://localhost")
    data = client.get("/api/erp-local/stock-movements?sku=demo.aparelho").json()
    exit_item = next(i for i in data["items"] if i["orders"])
    assert Decimal(exit_item["physical_delta"]) == -1
    assert exit_item["orders"] == [
        {
            "number": "310204",
            "quantity": "1",
            "shipped_at": (seen - timedelta(seconds=30))
            .astimezone(UTC)
            .isoformat(timespec="seconds"),
        }
    ]
    assert data["reserving"] == [
        {"number": "310420", "quantity": "2"},
        {"number": "310306", "quantity": "1"},
    ]
