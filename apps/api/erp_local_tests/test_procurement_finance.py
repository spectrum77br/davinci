import json
from decimal import Decimal

import pytest
from test_core import balance, order, purchase, run

from app.erp_local import finance, procurement
from app.erp_local.core import ERPError


def details(erp):
    with erp.connection() as db:
        return finance.snapshot(db), procurement.snapshot(erp, db)


def test_partial_receipts_preserve_separate_stock_accounts_and_exact_landed_cost(erp):
    run(
        erp,
        "valuation.set",
        {"sku": "demo.aparelho", "depot": "geral", "unit_cost": "10", "reason": "conferência"},
    )
    p = run(
        erp,
        "purchase.create",
        {
            "supplier": "Teste",
            "depot": "geral",
            "items": [{"sku": "demo.aparelho", "quantity": "2", "unit_price": "20"}],
            "freight": "5",
            "discount": "3",
            "charges": "2",
            "installments": [
                {"due_date": "2026-11-10", "amount": "22"},
                {"due_date": "2026-12-10", "amount": "22"},
            ],
        },
    )
    assert not erp.snapshot()["accounts"] and balance(erp) == (8, 0, 8)
    r = run(
        erp,
        "purchase.receive",
        {
            "id": p["id"],
            "receipt_id": "lote1",
            "lines": [{"line": 0, "quantity": "1", "lot": "L001"}],
        },
    )
    assert balance(erp) == (9, 0, 9)
    assert not erp.snapshot()["accounts"]
    assert Decimal(details(erp)[1]["values"][0]["value"]) == 102
    assert run(
        erp,
        "purchase.receive",
        {
            "id": p["id"],
            "receipt_id": "lote1",
            "lines": [{"line": 0, "quantity": "1", "lot": "L001"}],
        },
    )["duplicate"]
    run(erp, "purchase.accounts", {"id": p["id"]})
    assert [Decimal(i["amount"]) for i in details(erp)[0]["installments"]] == [22, 22]
    run(erp, "purchase.stock", {"id": p["id"]})
    assert balance(erp) == (10, 0, 10)
    assert Decimal(details(erp)[1]["values"][0]["value"]) == 124
    run(erp, "purchase.reverse_receipt", {"id": r["id"]})
    assert balance(erp) == (9, 0, 9)
    assert Decimal(details(erp)[1]["values"][0]["value"]) == 102


def test_receipt_rejects_over_receipt_and_conflicting_identity_atomically(erp):
    p = purchase(erp)
    payload = {"id": p["id"], "receipt_id": "R1", "lines": [{"line": 0, "quantity": "3"}]}
    before = erp.snapshot()
    with pytest.raises(ERPError):
        run(erp, "purchase.receive", payload)
    assert erp.snapshot() == before
    payload["lines"][0]["quantity"] = "1"
    run(erp, "purchase.receive", payload)
    payload["lines"][0]["quantity"] = "2"
    with pytest.raises(ERPError, match="outros dados"):
        run(erp, "purchase.receive", payload)
    assert balance(erp) == (9, 0, 9)


def test_cent_allocation_conserves_total_and_is_deterministic():
    assert procurement.allocate(Decimal(".01"), [Decimal(1)] * 3) == [
        Decimal(".01"),
        Decimal(0),
        Decimal(0),
    ]
    for total in ["0", "0.01", "10.07", "9999.99"]:
        parts = procurement.allocate(Decimal(total), [Decimal(2), Decimal(3), Decimal(4)])
        assert sum(parts) == Decimal(total)
        assert all(v == v.quantize(Decimal(".01")) for v in parts)


def test_missing_opening_cost_is_not_invented_and_transfers_preserve_value(erp):
    assert (
        next(v for v in details(erp)[1]["values"] if v["sku"] == "demo.aparelho")["value"] is None
    )
    run(
        erp,
        "valuation.set",
        {"sku": "demo.aparelho", "depot": "geral", "unit_cost": "12.5", "reason": "inventário"},
    )
    run(
        erp,
        "stock.transfer",
        {
            "sku": "demo.aparelho",
            "depot": "geral",
            "destination": "retornos",
            "quantity": "3",
            "reason": "transferência",
        },
    )
    values = [Decimal(v["value"]) for v in details(erp)[1]["values"] if v["sku"] == "demo.aparelho"]
    assert sum(values) == 100
    assert sorted(values) == [Decimal("37.5"), Decimal("62.5")]


def test_partial_payment_fees_reconciliation_and_reversal_do_not_move_stock(erp):
    o = order(erp, quantity="2")
    run(erp, "order.ship", {"id": o["id"]})
    before = balance(erp)
    account = erp.snapshot()["accounts"][0]
    run(erp, "bank.create", {"id": "caixa", "name": "Caixa local", "opening": "100"})
    run(
        erp,
        "finance.schedule",
        {
            "id": account["id"],
            "installments": [
                {"due_date": "2026-11-10", "amount": "10"},
                {"due_date": "2026-12-10", "amount": "10"},
            ],
        },
    )
    installment = details(erp)[0]["installments"][0]
    payload = {
        "installment_id": installment["id"],
        "amount": "6",
        "fee": "1",
        "discount": ".50",
        "interest": ".25",
        "bank_id": "caixa",
        "external_id": "COMPROVANTE-1",
    }
    payment = run(erp, "finance.pay", payload)
    assert payment["cash"] == "4.75" and erp.snapshot()["accounts"][0]["status"] == "partial"
    assert details(erp)[0]["banks"][0]["balance"] == "104.75"
    assert run(erp, "finance.pay", payload)["duplicate"]
    assert balance(erp) == before
    with pytest.raises(ERPError):
        run(
            erp,
            "finance.reconcile",
            {"id": payment["id"], "statement_line": "linha1", "amount": "6"},
        )
    match = run(
        erp,
        "finance.reconcile",
        {"id": payment["id"], "statement_line": "linha1", "amount": "4.75"},
    )
    with pytest.raises(ERPError):
        run(erp, "finance.reverse", {"id": payment["id"]})
    run(erp, "finance.unreconcile", {"id": match["id"]})
    run(erp, "finance.reverse", {"id": payment["id"]})
    assert erp.snapshot()["accounts"][0]["status"] == "open"
    assert details(erp)[0]["banks"][0]["balance"] == "100.00"


def test_partial_payable_blocks_account_reversal_and_schedule_changes(erp):
    p = purchase(erp)
    run(erp, "purchase.accounts", {"id": p["id"]})
    account = erp.snapshot()["accounts"][0]
    installment = details(erp)[0]["installments"][0]
    payment = run(
        erp,
        "finance.pay",
        {"installment_id": installment["id"], "amount": "1", "external_id": "p1"},
    )
    with pytest.raises(ERPError):
        run(erp, "purchase.reverse_accounts", {"id": p["id"]})
    with pytest.raises(ERPError):
        run(
            erp,
            "finance.schedule",
            {"id": account["id"], "installments": [{"due_date": "2027-01-01", "amount": "24.60"}]},
        )
    run(erp, "finance.reverse", {"id": payment["id"]})
    run(erp, "purchase.reverse_accounts", {"id": p["id"]})
    assert not details(erp)[0]["installments"]
    run(erp, "purchase.accounts", {"id": p["id"]})
    assert len(details(erp)[0]["installments"]) == 1


def test_partial_paid_return_refunds_only_paid_principal_and_cancels_unpaid_balance(erp):
    o = order(erp)
    run(erp, "order.ship", {"id": o["id"]})
    installment = details(erp)[0]["installments"][0]
    payment = run(
        erp,
        "finance.pay",
        {"installment_id": installment["id"], "amount": "4", "external_id": "partial"},
    )
    run(erp, "order.return", {"id": o["id"], "destination": "retornos"})
    accounts = {a["kind"]: a for a in erp.snapshot()["accounts"]}
    assert accounts["receivable"]["status"] == "cancelled"
    assert Decimal(accounts["refund"]["amount"]) == 4
    assert all(i["kind"] == "refund" for i in details(erp)[0]["installments"])
    with pytest.raises(ERPError) as error:
        run(erp, "finance.reverse", {"id": payment["id"]})
    assert error.value.code == "return_finance_review"


def test_non_sellable_physical_component_can_supply_a_kit_but_not_a_direct_sale(erp):
    with erp.connection() as db:
        db.execute(
            "INSERT INTO catalog_origin VALUES (?,?)",
            (
                "demo.aparelho",
                json.dumps({"catalog_state": "ready", "stock_state": "known", "sellable": False}),
            ),
        )
    run(
        erp,
        "product.create",
        {
            "sku": "nested.inactive",
            "name": "Kit",
            "kind": "kit",
            "components": [{"sku": "demo.aparelho", "quantity": "1"}],
        },
    )
    order(erp, external="kit-with-component", sku="nested.inactive")
    assert balance(erp) == (8, 1, 7)
    with pytest.raises(ERPError):
        order(erp, external="direct", sku="demo.aparelho")


def test_sales_and_returns_keep_original_issue_cost_after_new_purchase(erp):
    run(
        erp,
        "valuation.set",
        {"sku": "demo.aparelho", "depot": "geral", "unit_cost": "10", "reason": "abertura"},
    )
    run(
        erp,
        "valuation.set",
        {"sku": "demo.fone", "depot": "geral", "unit_cost": "5", "reason": "abertura"},
    )
    o = order(erp)
    run(erp, "order.ship", {"id": o["id"]})
    p = purchase(erp, [{"sku": "demo.aparelho", "quantity": "2", "unit_price": "30"}])
    run(erp, "purchase.stock", {"id": p["id"]})
    run(erp, "order.return", {"id": o["id"], "destination": "retornos"})
    values = {(v["sku"], v["depot"]): v for v in details(erp)[1]["values"]}
    assert Decimal(values["demo.aparelho", "retornos"]["value"]) == 10
    assert Decimal(values["demo.fone", "retornos"]["value"]) == 5


def test_invalid_financial_values_leave_no_partial_ledger_entries(erp):
    p = purchase(erp)
    run(erp, "purchase.accounts", {"id": p["id"]})
    installment = details(erp)[0]["installments"][0]
    for amount in ["NaN", "0", "25"]:
        with pytest.raises(ERPError):
            run(
                erp,
                "finance.pay",
                {
                    "installment_id": installment["id"],
                    "amount": amount,
                    "external_id": "bad-" + amount,
                },
            )
    assert not details(erp)[0]["payments"]


def test_repair_identity_requires_exact_live_id_sku_status_and_format(tmp_path):
    from test_catalog_repair import setup

    from app.erp_local.catalog_repair import repair_missing_catalog

    erp, read = setup(tmp_path)
    with erp.connection() as db:
        origin = json.loads(
            db.execute("SELECT data FROM catalog_origin WHERE sku='real.phone'").fetchone()[0]
        )
        origin["catalog_state"] = "review"
        db.execute("UPDATE catalog_origin SET data=? WHERE sku='real.phone'", (json.dumps(origin),))
        report = json.loads(db.execute("SELECT data FROM catalog_import").fetchone()[0])
        report["issues"].append({"sku": "real.phone", "code": "component_identity"})
        db.execute("UPDATE catalog_import SET data=?", (json.dumps(report),))
    read["structures"].append(
        {
            "id": "1",
            "sku": "wrong.sku",
            "name": "Phone",
            "format": "S",
            "status": "A",
            "components": [],
        }
    )
    assert not repair_missing_catalog(erp, read)["identities_confirmed"]
    read["structures"][-1]["sku"] = "real.phone"
    assert repair_missing_catalog(erp, read)["identities_confirmed"] == ["real.phone"]


def test_transfer_of_unknown_cost_does_not_invent_the_destinations_known_average(erp):
    run(
        erp,
        "stock.post",
        {
            "sku": "demo.aparelho",
            "depot": "retornos",
            "type": "E",
            "quantity": "1",
            "reason": "test",
            "unit_cost": "12",
        },
    )
    run(
        erp,
        "stock.transfer",
        {
            "sku": "demo.aparelho",
            "depot": "geral",
            "destination": "retornos",
            "quantity": "1",
            "reason": "test",
        },
    )
    value = next(
        v
        for v in details(erp)[1]["values"]
        if v["sku"] == "demo.aparelho" and v["depot"] == "retornos"
    )
    assert value["value"] is None and value["average"] is None


def test_receipts_with_recurring_unit_cost_preserve_exact_total_even_after_reverse_and_repost(erp):
    run(
        erp,
        "valuation.set",
        {"sku": "demo.aparelho", "depot": "geral", "unit_cost": "0", "reason": "abertura"},
    )
    # Freight makes the landed cost 10/3 per unit, which has no finite decimal expansion.
    # The purchase closes at exactly 10.00.
    p = run(
        erp,
        "purchase.create",
        {
            "supplier": "Teste",
            "depot": "geral",
            "freight": "1",
            "items": [{"sku": "demo.aparelho", "quantity": "3", "unit_price": "3"}],
        },
    )
    receipts = []
    for i in range(3):
        receipts.append(
            run(
                erp,
                "purchase.receive",
                {"id": p["id"], "receipt_id": str(i), "lines": [{"line": 0, "quantity": "1"}]},
            )
        )
    assert (
        Decimal(next(v for v in details(erp)[1]["values"] if v["sku"] == "demo.aparelho")["value"])
        == 10
    )
    run(erp, "purchase.reverse_receipt", {"id": receipts[1]["id"]})
    run(
        erp,
        "purchase.receive",
        {"id": p["id"], "receipt_id": "repost", "lines": [{"line": 0, "quantity": "1"}]},
    )
    assert (
        Decimal(next(v for v in details(erp)[1]["values"] if v["sku"] == "demo.aparelho")["value"])
        == 10
    )


def test_legacy_full_receipt_is_preserved_by_financial_reversal_and_cannot_be_received_again(erp):
    p = purchase(erp)
    run(erp, "purchase.stock", {"id": p["id"]})
    run(erp, "purchase.accounts", {"id": p["id"]})
    with erp.connection() as db:
        db.execute("DELETE FROM purchase_receipts WHERE document_id=?", (p["id"],))
    run(erp, "purchase.reverse_accounts", {"id": p["id"]})
    assert erp.snapshot()["purchases"][0]["data"]["stock_posted"] is True
    assert balance(erp) == (10, 0, 10)
    with pytest.raises(ERPError):
        run(
            erp,
            "purchase.receive",
            {"id": p["id"], "receipt_id": "new", "lines": [{"line": 0, "quantity": "1"}]},
        )


def test_opening_paid_fact_requires_reconciliation_instead_of_being_reversed_as_new_payment(erp):
    from app.erp_local.core import LocalERP

    o = order(erp)
    run(erp, "order.ship", {"id": o["id"]})
    account = erp.snapshot()["accounts"][0]
    with erp.connection() as db:
        db.execute("DELETE FROM finance_installments WHERE account_id=?", (account["id"],))
        db.execute("UPDATE accounts SET status='paid' WHERE id=?", (account["id"],))
    reopened = LocalERP(erp.database)
    opening = details(reopened)[0]["payments"][0]
    with pytest.raises(ERPError) as error:
        run(reopened, "finance.reverse", {"id": opening["id"]})
    assert error.value.code == "opening_finance_review"
    assert reopened.snapshot()["accounts"][0]["status"] == "paid"
