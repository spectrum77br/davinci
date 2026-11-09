"""Postage objects like Bling's list: sales in the shipping phase, and those already labelled."""

import json

from test_store_sales import observe

from app.erp_local import store_objects


def remember(erp, table, number, value):
    with erp.connection() as db:
        if table == "store_tracking":
            db.execute(
                "INSERT INTO store_tracking VALUES ('ml','conta',?,?,?)",
                (number, json.dumps(value), "2026-11-09T11:00:00"),
            )
        else:
            db.execute(
                "INSERT INTO store_labels VALUES ('ml','conta',?,'pdf',?,'{}',?)",
                (number, b"%PDF", "2026-11-09T11:00:00"),
            )


def test_objects_are_sales_to_ship_or_already_labelled(erp):
    observe(erp, "O1", "paid")
    observe(erp, "O2", "cancelled", created="2026-11-08T10:00:00")
    observe(erp, "O3", "delivered", created="2026-11-07T10:00:00")
    observe(erp, "O4", "shipped", created="2026-11-06T10:00:00")
    remember(erp, "store_labels", "O1", None)
    remember(erp, "store_tracking", "O4", {"tracking_number": "BR123", "status": "shipped"})
    with erp.connection() as db:
        result = store_objects.listing(db)
    by_number = {o["number"]: o for o in result["items"]}
    assert set(by_number) == {"O1", "O4"} and result["pending"] == 1
    assert by_number["O1"]["situation"] == "Não postado" and by_number["O1"]["label_at"]
    assert by_number["O1"]["service"] == "Mercado Envios" and by_number["O1"]["buyer"] == ""
    assert by_number["O4"]["situation"] == "Postado" and by_number["O4"]["tracking"] == "BR123"
    assert result["stores"] == [{"channel": "ml", "store": "conta", "name": "ML Teste"}]


def test_filter_by_store_and_search_by_tracking(erp):
    observe(erp, "P1", "paid")
    observe(erp, "P2", "shipped")
    remember(erp, "store_tracking", "P2", {"tracking_number": "BR999", "status": ""})
    with erp.connection() as db:
        assert store_objects.listing(db, "ml", "conta")["total"] == 2
        assert store_objects.listing(db, "shopee")["total"] == 0
        found = store_objects.listing(db, query="br999")
    assert [o["number"] for o in found["items"]] == ["P2"]


def test_listing_writes_nothing(erp):
    observe(erp, "Q1", "paid")
    with erp.connection() as db:
        before = db.execute("SELECT count(*) FROM documents").fetchone()[0]
        store_objects.listing(db)
        assert db.execute("SELECT count(*) FROM documents").fetchone()[0] == before
