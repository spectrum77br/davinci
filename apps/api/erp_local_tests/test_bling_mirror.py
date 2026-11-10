"""While Bling owns the stores, the ERP shows Bling's stock and situations as they are now."""

import json

from app.erp_local import bling_mirror


def link(erp, sku, source):
    with erp.connection() as db:
        db.execute(
            "INSERT OR REPLACE INTO catalog_origin VALUES (?,?)",
            (
                sku,
                json.dumps(
                    {
                        "bling_product_id": source,
                        "catalog_state": "ready",
                        "sellable": True,
                        "stock_state": "pending",
                    }
                ),
            ),
        )


def physical(erp, sku):
    with erp.connection() as db:
        return erp.balance(db, sku, "geral")[0]


def reserved(erp, sku):
    with erp.connection() as db:
        return erp.balance(db, sku, "geral")[1]


def test_stock_follows_bling_and_keeps_its_reservation_beside(erp):
    link(erp, "demo.aparelho", "100")
    rows = [{"id": "100", "available": "7", "reserved": "2", "updated_at": "2026-11-10 10:00"}]
    assert bling_mirror.apply_stock(erp, rows)["changed"] == 1
    assert physical(erp, "demo.aparelho") == 9 and reserved(erp, "demo.aparelho") == 2
    assert bling_mirror.apply_stock(erp, rows)["same"] == 1
    with erp.connection() as db:
        assert bling_mirror.stock_of(db, "demo.aparelho")["reserved"] == "2"
    later = [{"id": "100", "available": "5", "reserved": "2", "updated_at": "2026-11-10 10:05"}]
    bling_mirror.apply_stock(erp, later)
    assert physical(erp, "demo.aparelho") == 7


def test_stock_mirror_stops_once_a_store_is_switched(erp):
    link(erp, "demo.aparelho", "100")
    before = physical(erp, "demo.aparelho")
    with erp.connection() as db:
        columns = [r[1] for r in db.execute("PRAGMA table_info(ingest_accounts)")]
        values = {"channel": "ml", "store": "c", "enabled": 1, "authority": "own", "failures": 0}
        db.execute(
            f"INSERT INTO ingest_accounts({','.join(columns)}) "  # noqa: S608
            f"VALUES ({','.join('?' * len(columns))})",
            [values.get(c, "2026-11-10") for c in columns],
        )
    rows = [{"id": "100", "available": "50", "reserved": "0", "updated_at": "x"}]
    assert bling_mirror.apply_stock(erp, rows)["state"] == "stopped"
    assert physical(erp, "demo.aparelho") == before


def test_situations_are_replaced_each_pass_and_one_failure_keeps_the_other(erp):
    from app.erp_local.core import ERPError

    bling_mirror.apply_orders(erp, [{"store_number": "A1", "number": "308001", "situation": "6"}])
    bling_mirror.apply_orders(erp, [{"store_number": "A2", "number": "308002", "situation": "15"}])
    with erp.connection() as db:
        assert bling_mirror.orders(db) == {"A2": {"number": "308002", "situation": "15"}}

    def reader(sql):
        if sql == bling_mirror.STOCK_SQL:
            raise ERPError("source_unavailable", "fora")
        return [{"store_number": "A3", "number": "1", "situation": "6"}]

    report = bling_mirror.run(erp, reader)
    assert report["estoque"] == {"state": "source_unavailable"}
    assert report["situacoes"] == {"orders": 1}
