"""Stock proof against Bling, and Mercado Livre shipments that already left."""

import json
from decimal import Decimal

from test_store_sales import observe

from app.erp_local import ml_shipping, native_ingest, stock_check, store_objects


def with_shipment(erp, external, shipment):
    with erp.connection() as db:
        row = db.execute(
            "SELECT id,data FROM native_order_observations WHERE external_id=?", (external,)
        ).fetchone()
        data = {**json.loads(row["data"]), "shipping_id": shipment}
        db.execute(
            "UPDATE native_order_observations SET data=? WHERE id=?", (json.dumps(data), row["id"])
        )


def link(erp, sku, source):
    with erp.connection() as db:
        db.execute(
            "INSERT OR REPLACE INTO catalog_origin VALUES (?,?)",
            (sku, json.dumps({"bling_product_id": source, "catalog_state": "ready"})),
        )


def test_shipped_ml_sale_stops_being_open_and_shows_posted(erp):
    observe(erp, "M1", "paid", created="2026-11-09T10:00:00+00:00")
    with_shipment(erp, "M1", "S1")
    with erp.connection() as db:
        assert ml_shipping.due(db) == {"conta": {"S1": "M1"}}
    calls = []

    def reader(**request):
        calls.append(request)
        return {"S1": {"status": "shipped", "tracking_number": "MEL1", "cost": "0"}}

    report = ml_shipping.sync(erp, reader)
    assert report == {"stores": 1, "read": 1, "left": 1, "failed": 0}
    assert calls == [{"channel": "ml", "store": "conta", "refs": ["S1"], "mode": "tracking"}]
    with erp.connection() as db:
        assert ml_shipping.due(db) == {}
        unit = native_ingest._units(db, "ml", "conta")["M1"]
        reasons = native_ingest._unit_reasons(db, "ml", "conta", {}, "M1", unit)
        objects = store_objects.listing(db)
    assert "not_open" in reasons
    assert objects["items"][0]["situation"] == "Postado" and objects["pending"] == 0


def test_shipment_read_failure_keeps_it_due(erp):
    observe(erp, "M2", "paid", created="2026-11-09T10:00:00+00:00")
    with_shipment(erp, "M2", "S2")

    def failing(**request):
        raise RuntimeError("store down")

    assert ml_shipping.sync(erp, failing)["failed"] == 1
    with erp.connection() as db:
        assert ml_shipping.due(db) == {"conta": {"S2": "M2"}}


def test_stock_report_matches_bling_when_sales_explain_the_change(erp):
    link(erp, "demo.aparelho", "100")
    stock_check.take(erp, [{"bling_product_id": "100", "stock": "10"}], "2026-11-09T08:00:00+00:00")
    observe(erp, "V1", "paid", created="2026-11-09T09:00:00+00:00")
    observe(erp, "V2", "cancelled", created="2026-11-09T09:30:00+00:00")
    observe(erp, "V3", "unpaid", created="2026-11-09T09:40:00+00:00")
    stock_check.take(erp, [{"bling_product_id": "100", "stock": "9"}], "2026-11-09T10:00:00+00:00")
    with erp.connection() as db:
        result = stock_check.report(db)
    assert result["sales_counted"] == 1 and result["families_moved"] == 1
    assert result["families_equal"] == 1 and result["differences"] == []


def test_a_sale_bling_took_outside_the_window_does_not_count_by_the_store_time(erp):
    link(erp, "demo.aparelho", "100")
    stock_check.take(erp, [{"bling_product_id": "100", "stock": "10"}], "2026-11-09T08:00:00+00:00")
    observe(erp, "V8", "paid", created="2026-11-09T09:00:00+00:00")
    stock_check.take(erp, [{"bling_product_id": "100", "stock": "10"}], "2026-11-09T10:00:00+00:00")

    def read(sql):
        return [
            {
                "key": "V8",
                "number": "8",
                "sku": "demo.aparelho",
                "quantity": 1,
                "taken_at": "2026-11-09 11:00:00+00",
            }
        ]

    with erp.connection() as db:
        result = stock_check.report(db, outside=stock_check.outside_reader(read))
    assert result["families_moved"] == 0 and result["differences"] == []


def test_stock_report_lists_what_sales_do_not_explain(erp):
    link(erp, "demo.aparelho", "100")
    stock_check.take(erp, [{"bling_product_id": "100", "stock": "10"}], "2026-11-09T08:00:00+00:00")
    stock_check.take(erp, [{"bling_product_id": "100", "stock": "15"}], "2026-11-09T10:00:00+00:00")
    with erp.connection() as db:
        result = stock_check.report(db)
        assert stock_check.report(db, "2026-11-09T08:00:00+00:00")["state"] == "ok"
    assert result["families_equal"] == 0
    assert result["differences"][0]["bling"] == "5" and result["differences"][0]["erp"] == "0"


def test_stock_report_waits_for_two_snapshots(erp):
    with erp.connection() as db:
        assert stock_check.report(db) == {"state": "waiting", "snapshots": 0}


def test_seller_share_of_coupons_is_what_bling_takes_off():
    payload = {
        "details": [
            {"type": "coupon", "items": [{"amounts": {"total": 3.0, "seller": 3.0}}]},
            {"type": "coupon", "items": [{"amounts": {"total": 35.2, "seller": 0.0}}]},
            {"type": "discount", "items": [{"amounts": {"total": 29.8, "seller": 29.8}}]},
        ]
    }
    assert ml_shipping.seller_coupon(payload) == 3


def test_discounts_are_read_after_the_freight_and_404_means_none(erp):
    observe(erp, "M3", "paid", created="2026-11-09T10:00:00+00:00")
    observe(erp, "M4", "paid", created="2026-11-09T10:00:00+00:00")
    with erp.connection() as db:
        for number in ("M3", "M4"):
            db.execute(
                "INSERT INTO store_tracking VALUES ('ml','conta',?,?,?)",
                (number, json.dumps({"status": "shipped", "cost": "9.99"}), "2026-11-09"),
            )
        assert ml_shipping.discounts_due(db) == {"conta": {"M3": "M3", "M4": "M4"}}

    def reader(**request):
        assert request["mode"] == "discounts"
        coupon = {"type": "coupon", "items": [{"amounts": {"seller": 12.0}}]}
        return {"M3": {"details": [coupon]}, "M4": {"error": "Client error '404 Not Found'"}}

    assert ml_shipping.sync_discounts(erp, reader) == {"stores": 1, "sales": 2, "failed": 0}
    with erp.connection() as db:
        assert ml_shipping.discounts_due(db) == {}
        unit = native_ingest._units(db, "ml", "conta")["M3"]
        charges = native_ingest.ml_charges(db, "conta", "M3", list(unit.values()))
    assert charges == {"freight": Decimal("9.99"), "coupon": Decimal("12.0")}


def test_sales_outside_the_read_stores_explain_their_part_and_read_ones_count_once(erp):
    link(erp, "demo.aparelho", "100")
    stock_check.take(erp, [{"bling_product_id": "100", "stock": "10"}], "2026-11-09T08:00:00+00:00")
    observe(erp, "V1", "paid", created="2026-11-09T09:00:00+00:00")
    stock_check.take(erp, [{"bling_product_id": "100", "stock": "7"}], "2026-11-09T10:00:00+00:00")
    asked = []

    def read(sql):
        asked.append(sql)
        return [
            # The read sale again (same store number): must not count twice.
            {
                "key": "V1",
                "number": "300001",
                "sku": "DEMO.APARELHO",
                "quantity": 1,
                "taken_at": "2026-11-09 09:30:00+00",
            },
            # Temu and an internal order: only Bling's copy has them.
            {
                "key": "PO-029-1",
                "number": "300002",
                "sku": "demo.aparelho",
                "quantity": 1,
                "taken_at": "2026-11-09 09:30:00+00",
            },
            {
                "key": "",
                "number": "300003",
                "sku": "demo.aparelho",
                "quantity": 1,
                "taken_at": "2026-11-09 09:30:00+00",
            },
        ]

    with erp.connection() as db:
        alone = stock_check.report(db)
        together = stock_check.report(db, outside=stock_check.outside_reader(read))
    assert alone["families_equal"] == 0
    assert together["families_equal"] == 1 and together["outside_orders"] == 2
    assert "2026-11-09T08:00:00+00:00" in asked[0] and "'12'" in asked[0]


def test_combo_kept_simple_follows_its_parts_and_is_not_a_difference(erp):
    with erp.connection() as db:
        db.execute("INSERT INTO products VALUES ('dg1.ci+a1.ci','Combo','simple','[]')")
    link(erp, "dg1.ci+a1.ci", "200")
    stock_check.take(erp, [{"bling_product_id": "200", "stock": "10"}], "2026-11-09T08:00:00+00:00")
    stock_check.take(erp, [{"bling_product_id": "200", "stock": "4"}], "2026-11-09T10:00:00+00:00")
    with erp.connection() as db:
        result = stock_check.report(db)
    assert result["differences"] == [] and result["combos_derived"] == 1


def test_stock_moved_without_a_sale_is_told_apart_from_a_real_difference(erp):
    link(erp, "demo.aparelho", "100")
    link(erp, "demo.fone", "101")
    stock_check.take(
        erp,
        [{"bling_product_id": "100", "stock": "10"}, {"bling_product_id": "101", "stock": "5"}],
        "2026-11-09T08:00:00+00:00",
    )
    observe(erp, "V1", "paid", created="2026-11-09T09:00:00+00:00")
    stock_check.take(
        erp,
        # Aparelho: one sale and a manual entry of 3 in Bling. Fone: Bling sold one, the ERP none.
        [{"bling_product_id": "100", "stock": "12"}, {"bling_product_id": "101", "stock": "4"}],
        "2026-11-09T10:00:00+00:00",
    )

    def read(sql):
        return [
            {
                "key": "V1",
                "number": "1",
                "sku": "demo.aparelho",
                "quantity": 1,
                "taken_at": "2026-11-09 09:30:00+00",
            },
            {
                "key": "OUTRA-LOJA-LIDA",
                "number": "2",
                "sku": "demo.fone",
                "quantity": 1,
                "taken_at": "2026-11-09 09:30:00+00",
            },
        ]

    with erp.connection() as db:
        result = stock_check.report(
            db,
            outside=lambda s, e, known: stock_check.outside_reader(read)(
                s, e, known | {"OUTRA-LOJA-LIDA"}
            ),
        )
    assert result["families_moved_without_sale"] == 1
    assert result["moved_without_sale"][0]["family"] == "demo.aparelho"
    assert [d["family"] for d in result["differences"]] == ["demo.fone"]


def test_combo_sale_takes_its_parts():
    from collections import defaultdict
    from decimal import Decimal

    kits = {"dg1.ci+a1.ci": ("simple", []), "dg1.ci": ("simple", []), "a1.ci": ("simple", [])}
    out = defaultdict(Decimal)
    stock_check._expand(kits, "dg1.ci+a1.ci", Decimal(2), out)
    assert out == {"dg1.ci": 2, "a1.ci": 2}


def test_a_sale_counts_when_bling_took_it_inside_the_window(erp):
    link(erp, "demo.aparelho", "100")
    # The store's time is before the window, but Bling only took the sale inside it.
    observe(erp, "V9", "paid", created="2026-11-09T07:00:00+00:00")
    stock_check.take(erp, [{"bling_product_id": "100", "stock": "10"}], "2026-11-09T08:00:00+00:00")
    stock_check.take(erp, [{"bling_product_id": "100", "stock": "9"}], "2026-11-09T10:00:00+00:00")

    def read(sql):
        return [
            {
                "key": "V9",
                "number": "9",
                "sku": "demo.aparelho",
                "quantity": 1,
                "taken_at": "2026-11-09 09:30:00+00",
            }
        ]

    with erp.connection() as db:
        result = stock_check.report(db, outside=stock_check.outside_reader(read))
    assert result["families_equal"] == 1 and result["differences"] == []


def test_a_second_bling_order_for_a_read_sale_is_bling_stock_of_its_own(erp):
    link(erp, "demo.aparelho", "100")
    stock_check.take(erp, [{"bling_product_id": "100", "stock": "10"}], "2026-11-09T08:00:00+00:00")
    observe(erp, "V7", "paid", created="2026-11-09T09:00:00+00:00")
    stock_check.take(erp, [{"bling_product_id": "100", "stock": "8"}], "2026-11-09T10:00:00+00:00")

    def read(sql):
        return [
            {
                "key": "V7",
                "number": "300100",
                "sku": "demo.aparelho",
                "quantity": 1,
                "taken_at": "2026-11-09 09:10:00+00",
            },
            # An exchange sent later under the same store number.
            {
                "key": "V7",
                "number": "300150",
                "sku": "demo.aparelho",
                "quantity": 1,
                "taken_at": "2026-11-09 09:40:00+00",
            },
        ]

    with erp.connection() as db:
        result = stock_check.report(db, outside=stock_check.outside_reader(read))
    assert result["families_equal"] == 1 and result["outside_orders"] == 1
