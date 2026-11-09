from decimal import Decimal

import pytest
from test_core import run
from test_sales import body, doc

from app.erp_local import finance, payment_methods
from app.erp_local.core import ERPError


def method(erp, **changes):
    return run(
        erp,
        "payment_method.save",
        {
            "data": {
                "name": "Condição local",
                "type": "boleto",
                "days": [0, 30, 60],
                "fee_percent": "2.5",
                "fee_fixed": "0.50",
                "retention_days": 2,
                **changes,
            }
        },
    )


def test_sales_freeze_terms_exact_cents_and_keep_cash_separate(erp):
    identifier = method(erp)["id"]
    order = run(erp, "order.save", body(payment_method_id=identifier, status="reserved"))["id"]
    frozen = doc(erp, order)["data"]
    assert [i["amount"] for i in frozen["installments"]] == ["4.00"] * 3
    assert [i["due_date"] for i in frozen["installments"]] == [
        "2026-11-05",
        "2026-12-05",
        "2027-01-04",
    ]
    assert frozen["payment_forecasts"][0]["fee"] == "0.60"
    assert not erp.snapshot()["accounts"]
    run(
        erp,
        "payment_method.save",
        {
            "id": identifier,
            "version": 1,
            "data": {
                "name": "Condição alterada",
                "status": "inactive",
                "days": [5],
            },
        },
    )
    run(erp, "order.accounts", {"id": order})
    with erp.connection() as db:
        state = finance.snapshot(db)
    assert len(state["installments"]) == 3
    assert not state["payments"]
    assert doc(erp, order)["data"]["payment_method"] == frozen["payment_method"]
    with pytest.raises(ERPError, match="indisponível"):
        run(erp, "order.save", body(external_id="OTHER", payment_method_id=identifier))


def test_plan_uses_explicit_holiday_calendar_and_rounds_principal(erp):
    identifier = method(erp, business_days=True, holidays=["2026-11-09"], days=[0, 1, 2])["id"]
    with erp.connection() as db:
        frozen = payment_methods.freeze(db, identifier)
    plan, forecast = payment_methods.plan(frozen, "10.01", "2026-11-06")
    assert [p["amount"] for p in plan] == ["3.34", "3.34", "3.33"]
    assert [p["due_date"] for p in plan] == ["2026-11-06", "2026-11-10", "2026-11-11"]
    assert forecast[0]["expected_on"] == "2026-11-11"
    assert sum(Decimal(p["amount"]) for p in plan) == Decimal("10.01")


@pytest.mark.parametrize(
    "change",
    [
        {"days": [30, 0]},
        {"days": [0, 0]},
        {"days": [True]},
        {"days": []},
        {"days": [3651]},
        {"fee_percent": "101"},
        {"fee_fixed": "-1"},
        {"retention_days": True},
        {"bank_id": "foreign-bank"},
        {"type": "pix", "integrated": True},
        {"institution_document": "12345678000100"},
    ],
)
def test_invalid_method_has_no_partial_effect(erp, change):
    before = erp.snapshot()
    with pytest.raises(ERPError):
        method(erp, **change)
    assert erp.snapshot() == before


def test_applicability_version_and_minimum_installment(erp):
    identifier = method(erp, applicability="payable")["id"]
    with erp.connection() as db, pytest.raises(ERPError, match="indisponível"):
        payment_methods.freeze(db, identifier)
    with pytest.raises(ERPError, match="mudou"):
        run(erp, "payment_method.save", {"id": identifier, "version": 0, "data": {"name": "X"}})
    with pytest.raises(ERPError, match="descrição"):
        method(erp)
    identifier = method(erp, name="Parcelas", days=[0, 30, 60])["id"]
    with erp.connection() as db:
        frozen = payment_methods.freeze(db, identifier)
    with pytest.raises(ERPError, match="centavo"):
        payment_methods.plan(frozen, "0.02", "2026-11-05")
