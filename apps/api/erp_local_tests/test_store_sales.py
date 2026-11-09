"""Read sales listed for viewing: what the store shows and what would stop each one."""

import json

from app.erp_local import store_sales


def observe(
    erp, external, status, *, pack=None, sku="demo.aparelho", created="2026-11-09T10:00:00"
):
    order = {
        "id": external,
        "status": status,
        "pack_id": pack,
        "created_at": created,
        "total": {"amount": "10.00", "currency": "BRL"},
        "items": [
            {
                "line_id": "1",
                "listing_id": "L",
                "variation_id": None,
                "seller_sku": sku,
                "quantity": 1,
                "unit_price": {"amount": "10.00", "currency": "BRL"},
            }
        ],
    }
    with erp.connection() as db:
        db.execute("PRAGMA foreign_keys=OFF")
        db.execute(
            "INSERT INTO store_policies VALUES ('ml','conta','identity_review',?,?)",
            (json.dumps({"name": "ML Teste"}), erp.now()),
        ) if not db.execute("SELECT 1 FROM store_policies WHERE store='conta'").fetchone() else None
        columns = [r[1] for r in db.execute("PRAGMA table_info(native_order_observations)")]
        values = {
            "id": external,
            "channel": "ml",
            "store": "conta",
            "external_id": external,
            "updated_at": created,
            "captured_at": created,
            "data": json.dumps(order),
        }
        db.execute(
            f"INSERT INTO native_order_observations({','.join(columns)}) "  # noqa: S608
            f"VALUES ({','.join('?' * len(columns))})",
            [values.get(c, "x") for c in columns],
        )


def test_each_sale_shows_store_status_and_what_stops_it(erp):
    observe(erp, "A1", "paid")
    observe(erp, "A2", "cancelled", created="2026-11-08T10:00:00")
    observe(erp, "A3", "paid", sku="fora-do-catalogo", created="2026-11-07T10:00:00")
    with erp.connection() as db:
        result = store_sales.listing(db)
    assert result["total"] == 3 and [s["number"] for s in result["items"]] == ["A1", "A2", "A3"]
    first, cancelled, unlinked = result["items"]
    assert first["store"] == "ML Teste" and first["blocked"] == ["Comprador não guardado"]
    assert cancelled["blocked"] == ["Cancelado na loja"]
    assert "Item sem vínculo com o catálogo" in unlinked["blocked"]
    assert first["buyer"] == ""


def test_search_and_pages(erp):
    for index in range(3):
        observe(erp, f"B{index}", "paid", created=f"2026-11-0{index + 1}T10:00:00")
    with erp.connection() as db:
        assert store_sales.listing(db, "b1")["total"] == 1
        page = store_sales.listing(db, offset=1, limit=1)
    assert page["total"] == 3 and len(page["items"]) == 1


def test_listing_creates_no_order_and_moves_no_stock(erp):
    observe(erp, "C1", "paid")
    with erp.connection() as db:
        before = db.execute("SELECT count(*) FROM documents").fetchone()[0]
        store_sales.listing(db)
        assert db.execute("SELECT count(*) FROM documents").fetchone()[0] == before
