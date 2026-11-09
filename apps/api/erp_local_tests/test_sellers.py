from decimal import Decimal

import pytest
from test_core import run
from test_sales import body, doc

from app.erp_local import finance, sellers
from app.erp_local.core import ERPError


def create(erp, release="proportional"):
    return run(
        erp,
        "seller.save",
        {
            "contact": {"data": {"name": "Vendedor fictício", "person_type": "F"}},
            "data": {"rate": "10", "release": release},
        },
    )["id"]


def setup_sale(erp, release="proportional"):
    seller = create(erp, release)
    sale = run(
        erp,
        "order.save",
        body(
            seller_id=seller,
            status="reserved",
            freight="10",
            discount="0",
            items=[{"sku": "demo.aparelho", "quantity": "1", "unit_price": "100"}],
        ),
    )["id"]
    account = run(erp, "order.accounts", {"id": sale})["account_id"]
    run(
        erp,
        "finance.schedule",
        {
            "id": account,
            "installments": [
                {"amount": "55", "due_date": "2026-11-05"},
                {"amount": "55", "due_date": "2026-12-05"},
            ],
        },
    )
    return seller, sale, account


def receipt(erp, account, amount="55", index=0):
    with erp.connection() as db:
        i = next(
            i
            for i in finance.snapshot(db)["installments"]
            if i["account_id"] == account and i["due_date"] == ["2026-11-05", "2026-12-05"][index]
        )
    return run(
        erp,
        "finance.pay",
        {"installment_id": i["id"], "amount": amount, "external_id": f"SALE-{index}-{amount}"},
    )["id"]


def entitlement(erp, identifier):
    with erp.connection() as db:
        return sellers.entitlement(db, identifier)


@pytest.mark.parametrize(
    "release,expected_before,expected_partial,expected_first",
    [
        ("proportional", "0", "1.82", "5.00"),
        ("invoiced", "10.00", "10.00", "10.00"),
        ("first", "0", "0", "10.00"),
        ("last", "0", "0", "0"),
    ],
)
def test_release_follows_recorded_principal_not_warehouse_state(
    erp, release, expected_before, expected_partial, expected_first
):
    _, sale, account = setup_sale(erp, release)
    assert Decimal(entitlement(erp, sale)["released"]) == Decimal(expected_before)
    receipt(erp, account, "20")
    assert Decimal(entitlement(erp, sale)["released"]) == Decimal(expected_partial)
    receipt(erp, account, "35")
    assert Decimal(entitlement(erp, sale)["released"]) == Decimal(expected_first)
    receipt(erp, account, index=1)
    assert Decimal(entitlement(erp, sale)["released"]) == 10
    assert doc(erp, sale)["status"] == "reserved"  # No physical dispatch was performed.


def test_payment_guard_also_applies_to_generic_finance_and_duplicate_retry(erp):
    _, sale, account = setup_sale(erp)
    commission = entitlement(erp, sale)["account_id"]
    with erp.connection() as db:
        installment = next(
            i for i in finance.snapshot(db)["installments"] if i["account_id"] == commission
        )
    payload = {"installment_id": installment["id"], "amount": "5", "external_id": "COMMISSION-001"}
    with pytest.raises(ERPError, match="liberada"):
        run(erp, "finance.pay", payload)
    receipt(erp, account)
    payment = run(erp, "finance.pay", payload)
    assert entitlement(erp, sale)["balance"] == "0.00"
    assert run(erp, "finance.pay", payload)["duplicate"]
    receipt(erp, account, index=1)
    run(erp, "finance.reverse", {"id": payment["id"]})
    assert entitlement(erp, sale)["balance"] == "10.00"


def test_cancellation_retains_commission_cash_and_exposes_recovery_without_repayment(erp):
    _, sale, _ = setup_sale(erp, "invoiced")
    commission = entitlement(erp, sale)["account_id"]
    run(erp, "bank.create", {"id": "cash", "name": "Caixa local", "opening": "100"})
    with erp.connection() as db:
        installment = next(
            i for i in finance.snapshot(db)["installments"] if i["account_id"] == commission
        )
    payment = run(
        erp,
        "finance.pay",
        {
            "installment_id": installment["id"],
            "amount": "10",
            "bank_id": "cash",
            "external_id": "COMMISSION-001",
        },
    )
    run(erp, "order.reverse_accounts", {"id": sale})
    with pytest.raises(ERPError, match="comissões pagas"):
        run(erp, "order.save", body(id=sale, version=doc(erp, sale)["data"]["version"]))
    run(erp, "order.cancel", {"id": sale})
    state = entitlement(erp, sale)
    assert state["recoverable"] == "10.00" and state["pending"] == "0"
    with erp.connection() as db:
        assert finance.snapshot(db)["banks"][0]["balance"] == "90.00"
    run(erp, "finance.reverse", {"id": payment["id"]})
    assert entitlement(erp, sale)["recoverable"] == "0"


def test_seller_edits_do_not_change_previous_sales_and_stale_contact_rolls_back(erp):
    seller, sale, _ = setup_sale(erp)
    frozen = doc(erp, sale)["data"]["seller"]
    with erp.connection() as db:
        current = sellers.snapshot(db)["sellers"][0]
    contact = current["contact"]
    changed = {
        "id": seller,
        "version": 1,
        "contact": {
            "id": contact["id"],
            "version": contact["version"],
            "data": {**contact["data"], "name": "Nome atualizado"},
        },
        "data": {"rate": "20", "release": "last", "status": "inactive"},
    }
    run(erp, "seller.save", changed)
    assert doc(erp, sale)["data"]["seller"] == frozen
    with pytest.raises(ERPError, match="inativo"):
        run(erp, "order.save", body(external_id="NEW", seller_id=seller))
    with pytest.raises(ERPError, match="mudou"):
        run(erp, "seller.save", {**changed, "version": 2})


def test_sales_installment_plan_is_never_reused_for_commission_amount(erp):
    seller = create(erp, "first")
    method = run(erp, "payment_method.save", {"data": {"name": "Local 30/60", "days": [30, 60]}})[
        "id"
    ]
    sale = run(
        erp, "order.save", body(seller_id=seller, payment_method_id=method, status="reserved")
    )["id"]
    run(erp, "order.accounts", {"id": sale})
    with erp.connection() as db:
        state = finance.snapshot(db)
        commission = entitlement(erp, sale)["account_id"]
        installments = [i for i in state["installments"] if i["account_id"] == commission]
    assert len(installments) == 1 and installments[0]["amount"] == "0.90"
