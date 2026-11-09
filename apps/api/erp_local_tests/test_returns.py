from decimal import Decimal

import pytest
from test_core import balance, run
from test_sales import body

from app.erp_local import finance, returns, sellers
from app.erp_local.core import ERPError
from app.erp_local.release import inspect_database


def setup(erp, *, paid="0", seller_id=None):
    sale = run(
        erp,
        "order.save",
        body(
            status="reserved",
            freight="0",
            discount="0",
            seller_id=seller_id,
            items=[{"sku": "demo.kit", "quantity": "2", "unit_price": "100"}],
        ),
    )["id"]
    account = run(erp, "order.accounts", {"id": sale})["account_id"]
    if Decimal(paid):
        with erp.connection() as db:
            installment = next(
                i for i in finance.snapshot(db)["installments"] if i["account_id"] == account
            )
        run(
            erp,
            "finance.pay",
            {"installment_id": installment["id"], "amount": paid, "external_id": "RECEBIMENTO-001"},
        )
    run(erp, "order.stock", {"id": sale})
    case = run(erp, "returns.open", {"id": sale, "reason": "Conferência fictícia"})["id"]
    return sale, case, account


def receive(erp, case, reference="FISICO-001", lines=None):
    return run(
        erp,
        "returns.receive",
        {
            "id": case,
            "receipt_id": reference,
            "lines": lines
            or [
                {
                    "sku": "demo.aparelho",
                    "quantity": "1",
                    "condition": "new",
                    "destination": "geral",
                }
            ],
        },
    )


def test_partial_kits_new_and_damaged_and_financial_close_are_independent(erp):
    sale, case, _ = setup(erp, paid="100")
    quarantine = run(
        erp, "depot.save", {"name": "Avarias", "purpose": "quarantine", "exclude_from_total": True}
    )["id"]
    receive(
        erp,
        case,
        lines=[
            {"sku": "demo.aparelho", "quantity": "1", "condition": "new", "destination": "geral"},
            {
                "sku": "demo.fone",
                "quantity": "1",
                "condition": "damaged",
                "destination": quarantine,
            },
        ],
    )
    assert balance(erp) == (7, 0, 7) and balance(erp, "demo.fone", quarantine) == (1, 0, 1)
    assert not any(a["kind"] == "refund" for a in erp.snapshot()["accounts"])
    with pytest.raises(ERPError, match="parcial"):
        run(erp, "returns.close", {"id": case, "financial": "cancel_sale"})
    refund = run(
        erp,
        "returns.refund",
        {"id": sale, "reference": "REF-1", "amount": "20", "reason": "Aprovação local"},
    )
    assert refund["external_payments"] is False
    receive(
        erp,
        case,
        reference="FISICO-002",
        lines=[
            {"sku": "demo.aparelho", "quantity": "1", "condition": "new", "destination": "geral"},
            {"sku": "demo.fone", "quantity": "1", "condition": "new", "destination": "geral"},
        ],
    )
    run(erp, "returns.close", {"id": case, "financial": "cancel_sale"})
    accounts = erp.snapshot()["accounts"]
    assert sum(Decimal(a["amount"]) for a in accounts if a["kind"] == "refund") == 100
    assert next(o for o in erp.snapshot()["orders"] if o["id"] == sale)["status"] == "returned"
    assert balance(erp) == (8, 0, 8) and balance(erp, "demo.fone") == (4, 0, 4)
    assert inspect_database(erp.database)["ledger_ok"]


def test_receipt_dedup_overreceipt_and_legacy_reversal_guards(erp):
    sale, case, _ = setup(erp)
    first = receive(erp, case)
    before = erp.snapshot()["movements"]
    assert receive(erp, case)["duplicate"]
    assert erp.snapshot()["movements"] == before
    with pytest.raises(ERPError, match="outros dados"):
        receive(
            erp,
            case,
            lines=[
                {
                    "sku": "demo.aparelho",
                    "quantity": "0.5",
                    "condition": "new",
                    "destination": "geral",
                }
            ],
        )
    with pytest.raises(ERPError, match="ultrapassa"):
        receive(
            erp,
            case,
            reference="FISICO-002",
            lines=[
                {
                    "sku": "demo.aparelho",
                    "quantity": "2",
                    "condition": "new",
                    "destination": "geral",
                }
            ],
        )
    with pytest.raises(ERPError, match="devolução"):
        run(erp, "order.return", {"id": sale, "destination": "geral"})
    with pytest.raises(ERPError, match="devolução"):
        run(erp, "order.reverse_stock", {"id": sale})
    run(erp, "returns.reverse", {"id": first["id"]})
    assert receive(erp, case)["status"] == "reversed"
    run(erp, "returns.cancel", {"id": case})
    run(erp, "order.reverse_stock", {"id": sale})
    run(erp, "order.cancel", {"id": sale})
    assert balance(erp) == (8, 0, 8)


def test_damaged_destination_and_mixed_invalid_lines_have_no_effect(erp):
    _, case, _ = setup(erp)
    before = erp.snapshot()
    with pytest.raises(ERPError, match="quarentena"):
        receive(
            erp,
            case,
            lines=[
                {
                    "sku": "demo.fone",
                    "quantity": "1",
                    "condition": "damaged",
                    "destination": "geral",
                }
            ],
        )
    with pytest.raises(ERPError, match="pertencente"):
        receive(
            erp,
            case,
            lines=[
                {
                    "sku": "demo.aparelho",
                    "quantity": "1",
                    "condition": "new",
                    "destination": "geral",
                },
                {"sku": "other-sku", "quantity": "1", "condition": "new", "destination": "geral"},
            ],
        )
    assert erp.snapshot() == before


def test_refund_ceiling_counts_all_promises_and_cancelled_request_releases_capacity(erp):
    sale, case, _ = setup(erp, paid="100")
    receive(erp, case)
    payload = {"id": sale, "reference": "REF-1", "amount": "60", "reason": "Aprovação local"}
    request = run(erp, "returns.refund", payload)
    assert run(erp, "returns.refund", payload)["duplicate"]
    with pytest.raises(ERPError, match="ultrapassa"):
        run(erp, "returns.refund", {**payload, "reference": "REF-2", "amount": "41"})
    run(erp, "finance.entry.cancel", {"id": request["id"]})
    run(erp, "returns.refund", {**payload, "reference": "REF-2", "amount": "100"})
    with erp.connection() as db:
        assert returns.refunded_total(db, sale) == 100


def test_approved_refund_blocks_reversing_customer_receipt_and_automatic_commission_payment(erp):
    seller = run(
        erp,
        "seller.save",
        {
            "contact": {"data": {"name": "Vendedor local"}},
            "data": {"rate": "10", "release": "invoiced"},
        },
    )["id"]
    sale, case, account = setup(erp, paid="100", seller_id=seller)
    receive(erp, case)
    run(
        erp,
        "returns.refund",
        {"id": sale, "reference": "REF-1", "amount": "20", "reason": "Aprovação local"},
    )
    with erp.connection() as db:
        state = finance.snapshot(db)
        incoming = next(
            p
            for p in state["payments"]
            if any(
                i["account_id"] == account and i["id"] == p["installment_id"]
                for i in state["installments"]
            )
        )
        commission = sellers.entitlement(db, sale)
        outgoing = next(
            i for i in state["installments"] if i["account_id"] == commission["account_id"]
        )
    assert commission["review"] is True and Decimal(outgoing["released_remaining"]) == 0
    with pytest.raises(ERPError, match="reembolsos"):
        run(erp, "finance.reverse", {"id": incoming["id"]})
    with pytest.raises(ERPError, match="comissão"):
        run(
            erp,
            "finance.pay",
            {"installment_id": outgoing["id"], "amount": "1", "external_id": "COM-1"},
        )


def test_same_sku_may_be_split_between_saleable_and_damaged_but_aggregate_cannot_exceed_dispatch(
    erp,
):
    _, case, _ = setup(erp)
    quarantine = run(
        erp, "depot.save", {"name": "Avarias", "purpose": "quarantine", "exclude_from_total": True}
    )["id"]
    lines = [
        {"sku": "demo.aparelho", "quantity": "1", "condition": "new", "destination": "geral"},
        {
            "sku": "demo.aparelho",
            "quantity": "2",
            "condition": "damaged",
            "destination": quarantine,
        },
    ]
    before = erp.snapshot()
    with pytest.raises(ERPError, match="ultrapassa"):
        receive(erp, case, lines=lines)
    assert erp.snapshot() == before
    lines[1]["quantity"] = "1"
    receive(erp, case, lines=lines)
    assert balance(erp) == (7, 0, 7) and balance(erp, depot=quarantine) == (1, 0, 1)
