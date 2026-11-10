from decimal import Decimal

import pytest
from test_core import balance, run

from app.erp_local import finance
from app.erp_local.core import ERPError


def payload(**changes):
    return {
        "kind": "payable",
        "reference": "LOCAL-EXPENSE-001",
        "party": "Fornecedor de teste",
        "history": "Despesa demonstrativa",
        "amount": "100.01",
        "issued_on": "2026-01-31",
        "competence_on": "2026-01-01",
        "due_date": "2026-01-31",
        "payment_method": "Transferência",
        "category": "Operação",
        "document_number": "D-1",
        "occurrence": "installments",
        "count": 3,
        **changes,
    }


def state(erp):
    with erp.connection() as db:
        return finance.snapshot(db)


def test_manual_installments_conserve_cents_month_end_and_do_not_move_stock(erp):
    before = balance(erp)
    result = run(erp, "finance.entry.create", payload())
    items = state(erp)["installments"]
    assert [i["due_date"] for i in items] == ["2026-01-31", "2026-02-28", "2026-03-31"]
    assert [i["amount"] for i in items] == ["33.34", "33.34", "33.33"]
    assert sum(Decimal(i["amount"]) for i in items) == Decimal("100.01")
    assert len(result["ids"]) == 3 and balance(erp) == before
    assert not state(erp)["payments"]
    assert run(erp, "finance.entry.create", payload())["duplicate"]
    assert len(state(erp)["manual_documents"]) == 3
    with pytest.raises(ERPError, match="outros dados"):
        run(erp, "finance.entry.create", payload(amount="100.02"))


def test_recurrence_repeats_charge_instead_of_dividing_and_supports_leap_day():
    plan = finance.entry_plan(Decimal("50"), "2024-02-29", "annual", 5)
    assert [r["due_date"] for r in plan] == [
        "2024-02-29",
        "2025-02-28",
        "2026-02-28",
        "2027-02-28",
        "2028-02-29",
    ]
    assert sum(Decimal(r["amount"]) for r in plan) == 250
    assert [
        r["due_date"] for r in finance.entry_plan(Decimal("10"), "2026-12-28", "weekly", 2)
    ] == ["2026-12-28", "2027-01-04"]


def test_manual_expense_payment_requires_reversal_before_cancellation(erp):
    result = run(erp, "finance.entry.create", payload(occurrence="once", count=1))
    run(erp, "bank.create", {"id": "caixa", "name": "Caixa teste", "opening": "200"})
    installment = state(erp)["installments"][0]
    payment = run(
        erp,
        "finance.pay",
        {
            "installment_id": installment["id"],
            "amount": "10.00",
            "external_id": "PAY-1",
            "bank_id": "caixa",
        },
    )
    assert state(erp)["banks"][0]["balance"] == "190.00"
    assert state(erp)["manual_documents"][0]["status"] == "partial"
    with pytest.raises(ERPError, match="Estorne"):
        run(erp, "finance.entry.cancel", {"id": result["ids"][0]})
    run(erp, "finance.reverse", {"id": payment["id"]})
    run(erp, "finance.entry.cancel", {"id": result["ids"][0]})
    assert state(erp)["installments"][0]["account_status"] == "cancelled"
    assert state(erp)["banks"][0]["balance"] == "200.00"
    with pytest.raises(ERPError, match="cancelada"):
        run(
            erp,
            "finance.pay",
            {"installment_id": installment["id"], "amount": "1", "external_id": "PAY-2"},
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"amount": "0.01"},
        {"count": 121},
        {"count": True},
        {"count": "3"},
        {"due_date": "2026-02-30"},
        {"kind": "refund"},
        {"occurrence": "invented"},
        {"due_date": "9999-12-31"},
        {"occurrence": "once"},
    ],
)
def test_invalid_series_rolls_back_all_accounts_and_events(erp, changes):
    before = erp.snapshot()
    with pytest.raises(ERPError):
        run(erp, "finance.entry.create", payload(**changes))
    assert erp.snapshot() == before
    assert not state(erp)["manual_documents"]


def test_bank_slip_line_and_tax_guide_are_kept_like_bling(erp):
    line = "23793.38128 60000.000003 00000.000400 1 84340000010001"
    run(
        erp,
        "finance.entry.create",
        payload(barcode=line, tax_code="2089", tax_period="10/2026", occurrence="once", count=1),
    )
    with erp.connection() as db:
        data = next(
            d
            for d in (dict(r) for r in db.execute("SELECT data FROM documents"))
            if "barcode" in d["data"]
        )
    assert "".join(c for c in line if c.isdigit()) in data["data"]
    assert '"tax_code":"2089"' in data["data"] and '"tax_period":"10/2026"' in data["data"]
    with pytest.raises(ERPError, match="linha digitável"):
        run(
            erp,
            "finance.entry.create",
            payload(reference="X-2", barcode="123", occurrence="once", count=1),
        )


def test_financial_account_is_kept_and_must_exist(erp):
    run(erp, "bank.create", {"id": "caixa-loja", "name": "Caixa da loja", "opening": "0"})
    run(erp, "finance.entry.create", payload(bank_id="caixa-loja", occurrence="once", count=1))
    with erp.connection() as db:
        stored = [r[0] for r in db.execute("SELECT data FROM documents") if "caixa-loja" in r[0]]
    assert stored and '"bank_id":"caixa-loja"' in stored[0]
    with pytest.raises(ERPError, match="Conta financeira"):
        run(
            erp,
            "finance.entry.create",
            payload(reference="X-3", bank_id="nao-existe", occurrence="once", count=1),
        )
