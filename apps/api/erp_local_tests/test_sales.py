from decimal import Decimal

import pytest
from test_core import balance, run

from app.erp_local import finance
from app.erp_local.core import ERPError


def body(**changes):
    return {
        "channel": "local",
        "store": "manual",
        "external_id": "MANUAL-001",
        "depot": "geral",
        "customer_name": "Cliente de demonstração",
        "status": "draft",
        "date": "2026-11-05",
        "items": [{"sku": "demo.kit", "quantity": "1", "unit_price": "10"}],
        "freight": "3",
        "discount": "1",
        "charges": "0",
        **changes,
    }


def doc(erp, identifier):
    return next(o for o in erp.snapshot()["orders"] if o["id"] == identifier)


def test_manual_save_stock_accounts_and_reversals_are_separate_and_repeatable(erp):
    before = balance(erp)
    order = run(erp, "order.save", body())
    identifier = order["id"]
    assert balance(erp) == before and not erp.snapshot()["accounts"]
    run(erp, "order.confirm", {"id": identifier})
    assert balance(erp) == (8, 1, 7)
    result = run(erp, "order.accounts", {"id": identifier})
    assert erp.snapshot()["accounts"][0]["amount"] == "12.00" and balance(erp) == (8, 1, 7)
    run(erp, "order.stock", {"id": identifier})
    assert balance(erp) == (7, 0, 7) and len(erp.snapshot()["accounts"]) == 1
    run(erp, "order.reverse_accounts", {"id": identifier})
    assert balance(erp) == (7, 0, 7)
    run(erp, "order.reverse_stock", {"id": identifier})
    assert balance(erp) == (8, 1, 7)
    assert run(erp, "order.accounts", {"id": identifier})["account_id"] == result["account_id"]
    run(erp, "order.stock", {"id": identifier})
    assert len(erp.snapshot()["accounts"]) == 1
    run(erp, "order.reverse_stock", {"id": identifier})
    run(erp, "order.cancel", {"id": identifier})
    assert balance(erp) == before
    assert erp.snapshot()["accounts"][0]["status"] == "cancelled"
    assert doc(erp, identifier)["data"]["situation"] == "cancelled"


def test_manual_stock_does_not_create_receivable_and_return_without_accounts_is_valid(erp):
    before = balance(erp)
    identifier = run(erp, "order.save", body(status="reserved"))["id"]
    run(erp, "order.stock", {"id": identifier})
    assert not erp.snapshot()["accounts"] and balance(erp) == (7, 0, 7)
    run(erp, "order.return", {"id": identifier, "destination": "geral"})
    assert balance(erp) == before and not erp.snapshot()["accounts"]


def test_reserved_edit_is_atomic_and_updates_only_the_orders_reservation(erp):
    identifier = run(erp, "order.save", body(status="reserved"))["id"]
    changed = body(
        status="reserved",
        id=identifier,
        version=1,
        items=[{"sku": "demo.kit", "quantity": "2", "unit_price": "11"}],
    )
    run(erp, "order.save", changed)
    assert balance(erp) == (8, 2, 6)
    assert doc(erp, identifier)["data"]["total"] == "24.00"
    before = erp.snapshot()
    with pytest.raises(ERPError):
        run(
            erp,
            "order.save",
            {
                **changed,
                "version": 2,
                "items": [{"sku": "demo.kit", "quantity": "9", "unit_price": "1"}],
            },
        )
    assert erp.snapshot() == before
    with pytest.raises(ERPError, match="mudou"):
        run(erp, "order.save", changed)


def test_finance_must_be_reversed_before_edit_and_can_be_relaunched_at_new_total(erp):
    identifier = run(erp, "order.save", body(status="reserved"))["id"]
    account = run(erp, "order.accounts", {"id": identifier})["account_id"]
    version = doc(erp, identifier)["data"]["version"]
    changed = body(status="reserved", id=identifier, version=version, freight="10")
    with pytest.raises(ERPError, match="Estorne"):
        run(erp, "order.save", changed)
    run(erp, "order.reverse_accounts", {"id": identifier})
    changed["version"] = doc(erp, identifier)["data"]["version"]
    run(erp, "order.save", changed)
    assert run(erp, "order.accounts", {"id": identifier})["account_id"] == account
    with erp.connection() as db:
        active = finance.snapshot(db)["installments"]
    assert sum(Decimal(i["amount"]) for i in active) == 19


def test_cancellation_after_receipt_creates_refund_obligation_without_moving_physical_stock(erp):
    identifier = run(erp, "order.save", body(status="reserved"))["id"]
    run(erp, "order.accounts", {"id": identifier})
    with erp.connection() as db:
        installment = finance.snapshot(db)["installments"][0]
    payment = run(
        erp,
        "finance.pay",
        {"installment_id": installment["id"], "amount": "5", "external_id": "LOCAL-RECEIPT-001"},
    )
    with pytest.raises(ERPError, match="Estorne"):
        run(erp, "order.reverse_accounts", {"id": identifier})
    run(erp, "order.cancel", {"id": identifier})
    assert balance(erp) == (8, 0, 8)
    refund = next(a for a in erp.snapshot()["accounts"] if a["kind"] == "refund")
    assert Decimal(refund["amount"]) == 5 and refund["status"] == "open"
    with pytest.raises(ERPError, match="reembolso"):
        run(erp, "finance.reverse", {"id": payment["id"]})


def test_confirmation_invalidates_stale_draft_and_blocks_silent_channel_promotion(erp):
    identifier = run(erp, "order.save", body())["id"]
    with pytest.raises(ERPError, match="digitação"):
        run(
            erp,
            "order.import",
            {
                k: v
                for k, v in body().items()
                if k
                in {"channel", "store", "external_id", "depot", "items", "customer_name", "date"}
            },
        )
    run(erp, "order.confirm", {"id": identifier})
    with pytest.raises(ERPError, match="mudou"):
        run(erp, "order.save", body(id=identifier, version=1))


def test_draft_cancel_never_releases_reservations_from_another_order(erp):
    draft = run(erp, "order.save", body())["id"]
    run(erp, "order.save", body(external_id="OTHER", status="reserved"))
    before = balance(erp)
    run(erp, "order.cancel", {"id": draft})
    assert balance(erp) == before


def test_business_situation_is_separate_from_stock_and_batch_failure_is_atomic(erp):
    first = run(erp, "order.save", body(status="reserved"))["id"]
    second = run(erp, "order.save", body(external_id="OTHER", status="reserved"))["id"]
    before = erp.snapshot()
    with pytest.raises(ERPError):
        run(
            erp,
            "orders.batch",
            {"ids": [first, second], "action": "situation", "status": "shipped"},
        )
    assert erp.snapshot() == before
    run(erp, "order.stock", {"id": first})
    before = erp.snapshot()
    with pytest.raises(ERPError):
        run(
            erp,
            "orders.batch",
            {"ids": [first, second], "action": "situation", "status": "shipped"},
        )
    assert erp.snapshot() == before
    run(erp, "order.situation", {"id": first, "status": "shipped"})
    assert doc(erp, first)["data"]["situation"] == "shipped"
    run(erp, "order.situation", {"id": second, "status": "draft"})
    assert doc(erp, second)["status"] == "draft"
