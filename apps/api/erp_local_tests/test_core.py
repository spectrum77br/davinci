import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import pytest

from app.erp_local.core import ERPError, LocalERP, number


def run(erp, kind, payload, key=None):
    return erp.command(key or str(uuid4()), kind, payload)


def balance(erp, sku="demo.aparelho", depot="geral"):
    p = next(p for p in erp.snapshot()["products"] if p["sku"] == sku)
    b = next(b for b in p["balances"] if b["depot"] == depot)
    return tuple(
        Decimal(b[k]) if b[k] is not None else None for k in ["physical", "reserved", "available"]
    )


def order(erp, external="1", sku="demo.kit", quantity="1", items=None, **kw):
    return run(
        erp,
        "order.import",
        {
            "channel": "local",
            "store": kw.get("store", "demo"),
            "external_id": external,
            "depot": "geral",
            "items": items or [{"sku": sku, "quantity": quantity, "unit_price": "10.00"}],
        },
    )


def purchase(erp, items=None):
    return run(
        erp,
        "purchase.create",
        {
            "supplier": "Demo",
            "depot": "geral",
            "items": items or [{"sku": "demo.aparelho", "quantity": "2", "unit_price": "12.30"}],
        },
    )


def stock(erp, kind="E", quantity="1", **kw):
    return run(
        erp,
        "stock.post",
        {
            "sku": kw.get("sku", "demo.aparelho"),
            "depot": "geral",
            "type": kind,
            "quantity": quantity,
            "reason": "teste",
        },
    )


def test_seed_is_fictitious_and_kit_is_derived(erp):
    assert len(erp.snapshot()["products"]) == 3
    assert balance(erp) == (8, 0, 8)
    assert balance(erp, "demo.kit") == (5, None, 5)
    assert erp.snapshot()["external_connections"] is False
    assert not {"app.main", "app.config", "app.db", "app.redis_client"} & sys.modules.keys()


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", "0", "0.0001", "99999999", None, {}])
def test_invalid_quantities_are_rejected(value):
    with pytest.raises(ERPError):
        number(value)


def test_decimal_prices_are_exact():
    assert number("0.10", money=True) + number("0.20", money=True) == Decimal("0.30")


def test_unit_prices_keep_four_decimals_and_order_totals_round_once(erp):
    o = order(erp, items=[{"sku": "demo.aparelho", "quantity": "3", "unit_price": "0.3333"}])
    run(erp, "order.ship", {"id": o["id"]})
    assert erp.snapshot()["accounts"][0]["amount"] == "1.00"


def test_entry_exit_and_balance_target(erp):
    stock(erp, "E", "2")
    stock(erp, "S", "3")
    stock(erp, "B", "4")
    assert balance(erp) == (4, 0, 4)
    stock(erp, "B", "0")
    assert balance(erp) == (0, 0, 0)


def test_negative_stock_does_not_leave_event_or_movement(erp):
    before = erp.snapshot()
    with pytest.raises(ERPError, match="Saldo livre"):
        stock(erp, "S", "9")
    assert erp.snapshot() == before


def test_physical_posting_to_virtual_kit_is_forbidden(erp):
    with pytest.raises(ERPError) as e:
        stock(erp, quantity="2", sku="demo.kit")
    assert e.value.code == "virtual_kit"


def test_transfer_is_atomic_and_preserves_total(erp):
    run(
        erp,
        "stock.transfer",
        {
            "sku": "demo.aparelho",
            "depot": "geral",
            "destination": "retornos",
            "quantity": "3",
            "reason": "teste",
        },
    )
    assert balance(erp) == (5, 0, 5)
    assert balance(erp, depot="retornos") == (3, 0, 3)
    before = erp.snapshot()
    with pytest.raises(ERPError):
        run(
            erp,
            "stock.transfer",
            {
                "sku": "demo.aparelho",
                "depot": "geral",
                "destination": "missing",
                "quantity": "1",
                "reason": "teste",
            },
        )
    assert erp.snapshot() == before


def test_idempotency_replays_without_duplicating(erp):
    payload = {
        "sku": "demo.aparelho",
        "depot": "geral",
        "type": "E",
        "quantity": "2",
        "reason": "teste",
    }
    a = run(erp, "stock.post", payload, "same")
    b = run(erp, "stock.post", payload, "same")
    assert not a["replayed"] and b["replayed"]
    assert balance(erp) == (10, 0, 10)
    assert len([e for e in erp.snapshot()["events"] if e["operation_key"] == "same"]) == 1


def test_idempotency_rejects_changed_payload(erp):
    run(erp, "depot.create", {"id": "one", "name": "One"}, "same")
    with pytest.raises(ERPError) as e:
        run(erp, "depot.create", {"id": "two", "name": "Two"}, "same")
    assert e.value.code == "idempotency_conflict"


def test_kit_and_simple_share_aggregated_component_reservations(erp):
    order(
        erp,
        items=[
            {"sku": "demo.kit", "quantity": "2", "unit_price": "10"},
            {"sku": "demo.aparelho", "quantity": "3", "unit_price": "10"},
        ],
    )
    assert balance(erp) == (8, 5, 3)
    assert balance(erp, "demo.fone") == (5, 2, 3)
    assert balance(erp, "demo.kit") == (5, None, 3)


def test_insufficient_second_component_rolls_back_first_component(erp):
    before = erp.snapshot()
    with pytest.raises(ERPError):
        order(erp, quantity="6")
    assert erp.snapshot() == before


def test_order_business_identity_prevents_duplicate_with_different_operation_keys(erp):
    first = order(erp)
    second = order(erp)
    assert first["id"] == second["id"] and second["duplicate"]
    assert balance(erp) == (8, 1, 7)
    with pytest.raises(ERPError) as e:
        order(erp, quantity="2")
    assert e.value.code == "order_conflict"


def test_same_order_number_in_other_store_is_distinct(erp):
    a = order(erp, store="one")
    b = order(erp, store="two")
    assert a["id"] != b["id"]
    assert balance(erp) == (8, 2, 6)


def test_cancel_releases_only_reservation_and_is_repeatable(erp):
    o = order(erp)
    run(erp, "order.cancel", {"id": o["id"]})
    run(erp, "order.cancel", {"id": o["id"]})
    assert balance(erp) == (8, 0, 8)
    assert erp.snapshot()["accounts"] == []
    order(erp)  # repeated import of a cancelled external order must not reserve again
    assert balance(erp) == (8, 0, 8)


def test_stock_balance_cannot_remove_reserved_pieces(erp):
    order(erp, quantity="2")
    before = erp.snapshot()
    with pytest.raises(ERPError):
        stock(erp, "B", "1")
    assert erp.snapshot() == before


def test_ship_releases_reservation_reduces_physical_and_creates_one_receivable(erp):
    o = order(erp, quantity="2")
    run(erp, "order.ship", {"id": o["id"]})
    run(erp, "order.ship", {"id": o["id"]})
    assert balance(erp) == (6, 0, 6)
    assert balance(erp, "demo.fone") == (3, 0, 3)
    accounts = erp.snapshot()["accounts"]
    assert len(accounts) == 1 and accounts[0]["kind"] == "receivable"
    assert Decimal(accounts[0]["amount"]) == 20
    with pytest.raises(ERPError):
        run(erp, "order.cancel", {"id": o["id"]})


def test_return_new_restocks_chosen_depot_and_cancels_open_receivable(erp):
    o = order(erp)
    run(erp, "order.ship", {"id": o["id"]})
    run(erp, "order.return", {"id": o["id"], "destination": "retornos"})
    run(erp, "order.return", {"id": o["id"], "destination": "retornos"})
    assert balance(erp, depot="retornos") == (1, 0, 1)
    assert erp.snapshot()["accounts"][0]["status"] == "cancelled"


def test_return_after_local_settlement_creates_refund_once(erp):
    o = order(erp)
    run(erp, "order.ship", {"id": o["id"]})
    run(erp, "account.settle", {"id": erp.snapshot()["accounts"][0]["id"]})
    run(erp, "order.return", {"id": o["id"], "destination": "retornos"})
    assert sorted(a["kind"] for a in erp.snapshot()["accounts"]) == ["receivable", "refund"]


def test_used_return_is_explicitly_not_implemented(erp):
    o = order(erp)
    with pytest.raises(ERPError) as e:
        run(erp, "order.return", {"id": o["id"], "destination": "retornos", "condition": "used"})
    assert e.value.code == "return_condition"


def test_purchase_save_stock_and_accounts_are_separate(erp):
    p = purchase(erp)
    assert balance(erp) == (8, 0, 8)
    assert erp.snapshot()["accounts"] == []
    run(erp, "purchase.stock", {"id": p["id"]})
    run(erp, "purchase.stock", {"id": p["id"]})
    assert balance(erp) == (10, 0, 10)
    assert erp.snapshot()["accounts"] == []
    run(erp, "purchase.accounts", {"id": p["id"]})
    run(erp, "purchase.accounts", {"id": p["id"]})
    assert len(erp.snapshot()["accounts"]) == 1
    assert erp.snapshot()["purchases"][0]["status"] == "received"
    assert Decimal(erp.snapshot()["accounts"][0]["amount"]) == Decimal("24.60")


def test_purchase_accounts_can_be_generated_before_stock(erp):
    p = purchase(erp)
    run(erp, "purchase.accounts", {"id": p["id"]})
    assert balance(erp) == (8, 0, 8)
    assert erp.snapshot()["purchases"][0]["status"] == "partial"


def test_purchase_reversals_are_independent_and_preserve_audit(erp):
    p = purchase(erp)
    for kind in [
        "purchase.stock",
        "purchase.accounts",
        "purchase.reverse_stock",
        "purchase.reverse_accounts",
    ]:
        run(erp, kind, {"id": p["id"]})
    assert balance(erp) == (8, 0, 8)
    assert erp.snapshot()["purchases"][0]["status"] == "open"
    assert erp.snapshot()["accounts"][0]["status"] == "cancelled"
    run(erp, "purchase.accounts", {"id": p["id"]})
    assert len(erp.snapshot()["accounts"]) == 1
    assert erp.snapshot()["accounts"][0]["status"] == "open"


def test_stock_reversal_fails_atomically_when_received_pieces_are_reserved(erp):
    p = purchase(erp)
    run(erp, "purchase.stock", {"id": p["id"]})
    order(erp, sku="demo.aparelho", quantity="10")
    before = erp.snapshot()
    with pytest.raises(ERPError):
        run(erp, "purchase.reverse_stock", {"id": p["id"]})
    assert erp.snapshot() == before


def test_settled_payable_cannot_be_reversed(erp):
    p = purchase(erp)
    run(erp, "purchase.accounts", {"id": p["id"]})
    run(erp, "account.settle", {"id": erp.snapshot()["accounts"][0]["id"]})
    with pytest.raises(ERPError) as e:
        run(erp, "purchase.reverse_accounts", {"id": p["id"]})
    assert e.value.code == "paid_account"


def test_no_partial_receipt_is_silently_accepted(erp):
    p = purchase(erp)
    with pytest.raises(ERPError) as e:
        run(erp, "purchase.stock", {"id": p["id"], "quantity": "1"})
    assert e.value.code == "unsupported_fields"


def test_kit_creation_flattens_nested_virtual_kits_into_shared_physical_components(erp):
    run(
        erp,
        "product.create",
        {
            "sku": "demo.double",
            "name": "Two phones",
            "kind": "kit",
            "components": [{"sku": "demo.aparelho", "quantity": "2"}],
        },
    )
    assert balance(erp, "demo.double") == (4, None, 4)
    run(
        erp,
        "product.create",
        {
            "sku": "demo.nested",
            "name": "Nested",
            "kind": "kit",
            "components": [{"sku": "demo.kit", "quantity": "2"}],
        },
    )
    assert balance(erp, "demo.nested") == (2, None, 2)
    order(erp, sku="demo.nested")
    assert balance(erp) == (8, 2, 6)
    assert balance(erp, "demo.fone") == (5, 2, 3)


def test_persistence_survives_reopening(erp):
    order(erp)
    reopened = LocalERP(erp.database)
    assert reopened.snapshot() == erp.snapshot()


def test_concurrent_orders_cannot_oversell_same_components(erp):
    barrier = Barrier(2)

    def worker(n):
        barrier.wait()
        try:
            return order(erp, external=str(n), quantity="3")["id"]
        except ERPError as e:
            return e.code

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(worker, [1, 2]))
    assert results.count("insufficient_stock") == 1
    assert balance(erp, "demo.fone") == (5, 3, 2)
    assert len(erp.snapshot()["orders"]) == 1


def test_concurrent_duplicate_delivery_applies_once(erp):
    barrier = Barrier(2)
    payload = {
        "sku": "demo.aparelho",
        "depot": "geral",
        "type": "E",
        "quantity": "2",
        "reason": "retry",
    }

    def worker(_n):
        barrier.wait()
        return run(erp, "stock.post", payload, "concurrent-retry")

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(worker, [1, 2]))
    assert sum(r["replayed"] for r in results) == 1
    assert balance(erp) == (10, 0, 10)


def test_ledger_reconciles_all_balances_after_full_cycle(erp):
    p = purchase(erp)
    run(erp, "purchase.stock", {"id": p["id"]})
    o = order(erp)
    run(erp, "order.ship", {"id": o["id"]})
    run(erp, "order.return", {"id": o["id"], "destination": "retornos"})
    snapshot = erp.snapshot()
    for product in snapshot["products"]:
        if product["kind"] != "simple":
            continue
        for b in product["balances"]:
            moves = [
                m
                for m in snapshot["movements"]
                if m["sku"] == product["sku"] and m["depot"] == b["depot"]
            ]
            assert sum(Decimal(m["physical_delta"]) for m in moves) == Decimal(b["physical"])
            assert sum(Decimal(m["reserved_delta"]) for m in moves) == Decimal(b["reserved"])


def test_existing_unrelated_database_is_rejected(tmp_path):
    path = tmp_path / "foreign.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE production (id INTEGER)")
    with pytest.raises(ERPError) as e:
        LocalERP(path)
    assert e.value.code == "foreign_database"
