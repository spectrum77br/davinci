"""The catalog follows Bling: new products come in; combos stay kits that take their parts."""

import json

from app.erp_local import catalog_follow


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


def row(source, sku, formato="S", situacao="A"):
    return {
        "id": "m" + source,
        "bling_product_id": source,
        "sku": sku,
        "name": "Produto " + sku,
        "formato": formato,
        "situacao": situacao,
        "price": "10.00",
        "cost_price": "4.00",
        "bling_cost_price": None,
        "updated_at": "2026-11-10T08:00:00+00:00",
    }


def kind(erp, sku):
    with erp.connection() as db:
        found = db.execute("SELECT kind FROM products WHERE sku=?", (sku,)).fetchone()
        return found["kind"] if found else None


def test_new_bling_products_come_in_linked_and_kits_find_their_parts(erp):
    link(erp, "demo.aparelho", "100")
    products = [row("100", "demo.aparelho"), row("900", "novo.ci"), row("901", "combo.ci", "E")]
    components = [
        {"kit_bling_product_id": "901", "component_bling_product_id": "100", "quantity": "1"},
        {"kit_bling_product_id": "901", "component_bling_product_id": "900", "quantity": "2"},
    ]
    report = catalog_follow.follow(erp, products, components)
    assert report == {"created": 2, "refused": {}}
    assert kind(erp, "novo.ci") == "simple" and kind(erp, "combo.ci") == "kit"
    with erp.connection() as db:
        origin = json.loads(
            db.execute("SELECT data FROM catalog_origin WHERE sku='novo.ci'").fetchone()[0]
        )
    assert origin["bling_product_id"] == "900"
    assert catalog_follow.follow(erp, products, components)["created"] == 0


def test_combos_stay_kits_and_a_new_combo_without_parts_is_reported(erp):
    link(erp, "demo.kit", "500")
    report = catalog_follow.follow(
        erp, [row("500", "demo.kit", "S"), row("902", "x1.ci+a1.ci")], []
    )
    assert report["created"] == 0 and "x1.ci+a1.ci" in report["refused"]
    assert kind(erp, "demo.kit") == "kit" and kind(erp, "x1.ci+a1.ci") is None


def status(erp, sku):
    from app.erp_local import product_details

    with erp.connection() as db:
        return product_details.details(db, sku)["status"]


def test_situation_follows_bling_and_listings_get_zero(erp):
    from app.erp_local.channels import preview_quantity

    link(erp, "demo.aparelho", "100")
    excluded = [row("100", "demo.aparelho", situacao="E")]
    assert catalog_follow.follow_status(erp, excluded) == {"changed": 1, "refused": {}}
    assert status(erp, "demo.aparelho") == "inactive"
    with erp.connection() as db:
        assert preview_quantity(erp, db, "demo.aparelho", "geral") == ("0", "product_inactive")
        origin = json.loads(
            db.execute("SELECT data FROM catalog_origin WHERE sku='demo.aparelho'").fetchone()[0]
        )
    assert origin["source_status"] == "E" and origin["sellable"] is False
    assert catalog_follow.follow_status(erp, excluded)["changed"] == 0
    # A duplicate active row in DaVinci keeps it active; Bling reactivating brings it back.
    back = [row("100", "demo.aparelho", situacao="I"), row("100", "demo.aparelho")]
    back[1]["updated_at"] = "2026-11-10T09:00:00+00:00"
    assert catalog_follow.follow_status(erp, back)["changed"] == 1
    assert status(erp, "demo.aparelho") == "active"


def test_situation_stops_once_a_store_is_switched(erp):
    link(erp, "demo.aparelho", "100")
    with erp.connection() as db:
        columns = [r[1] for r in db.execute("PRAGMA table_info(ingest_accounts)")]
        values = {"channel": "ml", "store": "c", "enabled": 1, "authority": "own", "failures": 0}
        db.execute(
            f"INSERT INTO ingest_accounts({','.join(columns)}) "  # noqa: S608
            f"VALUES ({','.join('?' * len(columns))})",
            [values.get(c, "2026-11-10") for c in columns],
        )
    report = catalog_follow.follow_status(erp, [row("100", "demo.aparelho", situacao="E")])
    assert report == {"state": "stopped"} and status(erp, "demo.aparelho") == "active"


def test_new_products_stop_coming_from_bling_once_a_store_is_switched(erp):
    with erp.connection() as db:
        columns = [r[1] for r in db.execute("PRAGMA table_info(ingest_accounts)")]
        values = {"channel": "ml", "store": "c", "enabled": 1, "authority": "own", "failures": 0}
        db.execute(
            f"INSERT INTO ingest_accounts({','.join(columns)}) "  # noqa: S608
            f"VALUES ({','.join('?' * len(columns))})",
            [values.get(c, "2026-11-10") for c in columns],
        )
    assert catalog_follow.follow(erp, [row("900", "novo.ci")], []) == {"state": "stopped"}
    assert kind(erp, "novo.ci") is None
