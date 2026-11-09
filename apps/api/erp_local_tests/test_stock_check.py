"""Stock proof against Bling, and Mercado Livre shipments that already left."""

import json

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
