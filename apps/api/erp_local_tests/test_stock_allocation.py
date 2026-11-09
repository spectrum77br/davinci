import copy
from decimal import Decimal

import pytest
from test_core import run
from test_sales import body, doc

from app.erp_local import release, stock_allocation
from app.erp_local.core import ERPError


def catalogue(erp):
    for lot in ["ci", "sp"]:
        for base in ["a", "b", "f"]:
            sku = f"{base}.{lot}"
            run(erp, "product.create", {"sku": sku, "name": sku, "kind": "simple"})
            run(
                erp,
                "stock.post",
                {
                    "sku": sku,
                    "depot": "geral",
                    "type": "E",
                    "quantity": "3",
                    "reason": "Abertura fictícia",
                },
            )
        for base in ["a", "b"]:
            sku = f"{base}.{lot}+f.{lot}"
            run(
                erp,
                "product.create",
                {
                    "sku": sku,
                    "name": sku,
                    "kind": "kit",
                    "components": [
                        {"sku": f"{base}.{lot}", "quantity": "1"},
                        {"sku": f"f.{lot}", "quantity": "1"},
                    ],
                },
            )


def sale(erp, status="reserved", two=False):
    catalogue(erp)
    items = [{"sku": "a.ci+f.ci", "quantity": "2", "unit_price": "10.125"}]
    if two:
        items.append({"sku": "b.ci+f.ci", "quantity": "1", "unit_price": "20"})
    return run(erp, "order.save", body(status=status, items=items))["id"]


def choice(erp, identifier, skus, version=None):
    return {
        "id": identifier,
        "version": version if version is not None else doc(erp, identifier)["data"]["version"],
        "items": [{"index": i, "sku": sku} for i, sku in enumerate(skus)],
        "reason": "Conferência manual dos lotes locais",
    }


def balances(erp, sku):
    with erp.connection() as db:
        return erp.balance(db, sku, "geral")


def test_lot_allocation_preserves_commercial_data_moves_only_own_reservations_and_is_idempotent(
    erp,
):
    identifier = sale(erp)
    original = copy.deepcopy(doc(erp, identifier)["data"])
    payload = choice(erp, identifier, ["a.sp+f.sp"])
    result = erp.command("allocation-1", "order.allocate_stock", payload)
    assert erp.command("allocation-1", "order.allocate_stock", payload)["replayed"]
    after = doc(erp, identifier)["data"]
    assert after["items"] == original["items"] and after["total"] == original["total"]
    assert after["requirements"] == {"a.sp": "2.000000", "f.sp": "2.000000"}
    assert balances(erp, "a.ci") == (Decimal(3), Decimal(0))
    assert balances(erp, "f.sp") == (Decimal(3), Decimal(2))
    assert result["version"] == original["version"] + 1
    assert not erp.snapshot()["accounts"]
    assert release.inspect_database(erp.database)["ledger_ok"]


def test_allocation_accounts_stock_reversal_and_return_use_frozen_physical_lot(erp):
    identifier = sale(erp)
    run(erp, "order.allocate_stock", choice(erp, identifier, ["a.sp+f.sp"]))
    run(erp, "order.accounts", {"id": identifier})
    assert erp.snapshot()["accounts"][0]["amount"] == doc(erp, identifier)["data"]["total"]
    run(erp, "order.stock", {"id": identifier})
    assert balances(erp, "a.sp") == (Decimal(1), Decimal(0))
    assert balances(erp, "a.ci") == (Decimal(3), Decimal(0))
    run(erp, "order.return", {"id": identifier, "destination": "geral"})
    assert balances(erp, "a.sp") == (Decimal(3), Decimal(0))
    assert balances(erp, "a.ci") == (Decimal(3), Decimal(0))


def test_shared_component_shortage_rolls_back_entire_lot_change_and_history(erp):
    identifier = sale(erp, two=True)
    run(
        erp,
        "stock.post",
        {"sku": "f.sp", "depot": "geral", "type": "S", "quantity": "1", "reason": "Saída fictícia"},
    )
    before = erp.snapshot()
    with pytest.raises(ERPError, match="Saldo livre insuficiente"):
        run(erp, "order.allocate_stock", choice(erp, identifier, ["a.sp+f.sp", "b.sp+f.sp"]))
    assert erp.snapshot() == before
    with erp.connection() as db:
        assert stock_allocation.history(db, identifier) == []


def test_a_similarly_named_kit_with_a_different_recipe_cannot_substitute_the_original(erp):
    identifier = sale(erp)
    with erp.connection() as db:
        # Simulates a divergent catalogue recipe under an otherwise matching SKU.
        db.execute(
            "UPDATE products SET components=? WHERE sku='a.sp+f.sp'",
            ('[{"sku":"b.sp","quantity":"1"},{"sku":"f.sp","quantity":"1"}]',),
        )
        view = stock_allocation.options(erp, db, identifier)
        assert "a.sp+f.sp" not in {o["sku"] for o in view["items"][0]["options"]}
    before = erp.snapshot()
    with pytest.raises(ERPError, match="composição"):
        run(erp, "order.allocate_stock", choice(erp, identifier, ["a.sp+f.sp"]))
    assert erp.snapshot() == before


def test_draft_allocation_never_reserves_until_confirmation_and_cancel_releases_target(erp):
    identifier = sale(erp, status="draft")
    run(erp, "order.allocate_stock", choice(erp, identifier, ["a.sp+f.sp"]))
    assert balances(erp, "a.sp") == (Decimal(3), Decimal(0))
    run(erp, "order.confirm", {"id": identifier})
    assert balances(erp, "a.sp") == (Decimal(3), Decimal(2))
    run(erp, "order.cancel", {"id": identifier})
    assert balances(erp, "a.sp") == (Decimal(3), Decimal(0))


def test_an_explicit_reset_unlocks_commercial_edit_without_silently_reverting_lot(erp):
    identifier = sale(erp)
    run(erp, "order.allocate_stock", choice(erp, identifier, ["a.sp+f.sp"]))
    with pytest.raises(ERPError, match="Retorne ao lote original"):
        run(erp, "order.save", body(id=identifier, version=2))
    run(erp, "order.allocate_stock", choice(erp, identifier, ["a.ci+f.ci"]))
    order = doc(erp, identifier)["data"]
    assert "stock_allocation" not in order and "fulfillment_items" not in order
    run(erp, "order.save", body(id=identifier, version=3))
    with erp.connection() as db:
        assert len(stock_allocation.history(db, identifier)) == 2


def test_stale_versions_financial_posting_and_logistics_block_lot_changes(erp):
    identifier = sale(erp)
    with pytest.raises(ERPError, match="mudou"):
        run(erp, "order.allocate_stock", choice(erp, identifier, ["a.sp+f.sp"], version=0))
    run(erp, "order.accounts", {"id": identifier})
    with pytest.raises(ERPError, match="Estorne as contas"):
        run(erp, "order.allocate_stock", choice(erp, identifier, ["a.sp+f.sp"]))
    run(erp, "order.reverse_accounts", {"id": identifier})
    run(erp, "logistics.prepare", {"id": identifier, "service": "Entrega de teste"})
    with pytest.raises(ERPError, match="separação"):
        run(erp, "order.allocate_stock", choice(erp, identifier, ["a.sp+f.sp"]))


def test_read_only_options_include_this_orders_reservation_and_never_claim_pooling(erp):
    identifier = sale(erp)
    before = erp.snapshot()
    with erp.connection() as db:
        view = stock_allocation.options(erp, db, identifier)
    assert erp.snapshot() == before
    assert {
        o["sku"]: Decimal(o["capacity_including_this_order"]) for o in view["items"][0]["options"]
    } == {
        "a.ci+f.ci": Decimal(3),
        "a.sp+f.sp": Decimal(3),
    }
    assert view["external_writes"] is False


def test_allocation_history_survives_verified_backup_and_restore(erp, tmp_path):
    identifier = sale(erp)
    run(erp, "order.allocate_stock", choice(erp, identifier, ["a.sp+f.sp"]))
    copy = tmp_path / "backup.sqlite3"
    restored = tmp_path / "restored.sqlite3"
    release.backup(erp.database, copy)
    assert release.restore(copy, restored)["restore_verified"]
    with release.connect(restored) as db:
        assert (
            stock_allocation.history(db, identifier)[0]["reason"]
            == "Conferência manual dos lotes locais"
        )
    assert release.inspect_database(restored)["ledger_ok"]


def test_lot_choice_keeps_target_tax_snapshot_separate_from_the_original_invoice_reference(erp):
    catalogue(erp)
    for sku, origin in [("a.ci+f.ci", "2"), ("a.sp+f.sp", "0")]:
        run(
            erp,
            "product.tax.save",
            {
                "sku": sku,
                "version": 0,
                "data": {"origin": origin, "ncm": "00000000"},
                "reason": "Classificação fictícia para testar preservação do lote",
            },
        )
    identifier = run(
        erp,
        "order.save",
        body(
            status="reserved",
            items=[
                {"sku": "a.ci+f.ci", "quantity": "1", "unit_price": "10"},
            ],
        ),
    )["id"]
    run(erp, "order.allocate_stock", choice(erp, identifier, ["a.sp+f.sp"]))
    order = doc(erp, identifier)["data"]
    assert order["fiscal_allocation_review"] is True
    assert order["product_snapshots"]["a.ci+f.ci"]["tax"]["data"]["origin"] == "2"
    assert order["fulfillment_product_snapshots"]["a.sp+f.sp"]["tax"]["data"]["origin"] == "0"
    run(
        erp,
        "product.tax.save",
        {
            "sku": "a.sp+f.sp",
            "version": 1,
            "data": {"origin": "8"},
            "reason": "Mudança posterior fictícia",
        },
    )
    assert (
        doc(erp, identifier)["data"]["fulfillment_product_snapshots"]["a.sp+f.sp"]["tax"]["data"][
            "origin"
        ]
        == "0"
    )
    assert release.inspect_database(erp.database)["ledger_ok"]
