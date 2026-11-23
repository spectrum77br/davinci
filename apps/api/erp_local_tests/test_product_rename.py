"""The ERP's own code change (T1c): same product, same number, and DaVinci hears it from the ERP."""

import json

import pytest

from app.erp_local import bling_compat, catalog_follow
from app.erp_local.core import ERPError


def outbox(erp, event="produto.alteracao"):
    with erp.connection() as db:
        return [
            json.loads(r[0])["dados"]
            for r in db.execute("SELECT body FROM compat_outbox WHERE event=?", (event,))
        ]


def switch_a_store(erp):
    with erp.connection() as db:
        db.execute(
            "INSERT INTO ingest_accounts(channel,store,enabled,authority,failures,updated_at) "
            "VALUES ('ml','loja-trocada',1,'own',0,?)",
            (erp.now(),),
        )


def test_while_bling_owns_the_catalog_the_code_is_changed_in_bling(erp):
    with pytest.raises(ERPError) as refused:
        erp.command("r1", "product.rename", {"from": "demo.aparelho", "to": "demo.novo"})
    assert refused.value.code == "product_rename_bling"
    with erp.connection() as db:
        assert db.execute("SELECT 1 FROM products WHERE sku='demo.aparelho'").fetchone()
    assert outbox(erp) == []


def test_own_change_keeps_stock_history_kits_and_number_and_tells_davinci(erp):
    switch_a_store(erp)
    with erp.connection() as db:
        number = bling_compat.numeric_id(db, "product", "demo.aparelho")
        db.commit()
        before = db.execute(
            "SELECT sum(physical),sum(reserved) FROM balances WHERE sku='demo.aparelho'"
        ).fetchone()
        history = db.execute("SELECT count(*) FROM movements WHERE sku='demo.aparelho'").fetchone()
        kits = [
            r[0]
            for r in db.execute("SELECT sku,components FROM products WHERE kind='kit'")
            if "demo.aparelho" in r[1]
        ]
    result = erp.command("r2", "product.rename", {"from": "demo.aparelho", "to": "demo.novo"})
    assert (result["from"], result["to"]) == ("demo.aparelho", "demo.novo")
    with erp.connection() as db:
        assert not db.execute("SELECT 1 FROM products WHERE sku='demo.aparelho'").fetchone()
        assert tuple(
            db.execute(
                "SELECT sum(physical),sum(reserved) FROM balances WHERE sku='demo.novo'"
            ).fetchone()
        ) == tuple(before)
        assert (
            db.execute("SELECT count(*) FROM movements WHERE sku='demo.novo'").fetchone()[0]
            == history[0]
        )
        for kit in kits:
            parts = json.loads(
                db.execute("SELECT components FROM products WHERE sku=?", (kit,)).fetchone()[0]
            )
            assert "demo.novo" in [p["sku"] for p in parts]
        assert bling_compat.numeric_id(db, "product", "demo.novo") == number
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    # DaVinci finds the product by the number it knows and takes the new code (webhooks.py).
    assert outbox(erp) == [{"id": number, "codigo": "demo.novo"}]


@pytest.mark.parametrize(
    ("to", "code"),
    [("demo.kit", "duplicate_sku"), ("demo.aparelho", "product_rename"), ("a b", "invalid_sku")],
)
def test_a_taken_same_or_invalid_code_is_refused(erp, to, code):
    switch_a_store(erp)
    with pytest.raises(ERPError) as refused:
        erp.command("r3-" + code, "product.rename", {"from": "demo.aparelho", "to": to})
    assert refused.value.code == code
    assert outbox(erp) == []


def test_following_a_code_changed_in_bling_also_queues_the_notice(erp):
    with erp.connection() as db:
        db.execute(
            "INSERT OR REPLACE INTO catalog_origin VALUES (?,?)",
            (
                "demo.aparelho",
                json.dumps(
                    {"bling_product_id": "77", "stock_state": "known", "catalog_state": "ready"}
                ),
            ),
        )
    rows = [{"bling_product_id": "77", "sku": "demo.renomeado", "situacao": "A"}]
    assert catalog_follow.follow_renames(erp, rows)["renamed"] == {
        "demo.aparelho": "demo.renomeado"
    }
    assert outbox(erp) == [{"id": 77, "codigo": "demo.renomeado"}]
