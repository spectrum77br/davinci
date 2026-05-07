"""Pricing calc engine (Fase 9b)."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from app.models import (
    CellStatus,
    Department,
    PricingAccount,
    PricingOverride,
    PricingPlatform,
    PricingProduct,
)
from app.services.pricing.calc import calculate


def _account(**kwargs) -> PricingAccount:
    base = {
        "id": uuid4(),
        "user_id": uuid4(),
        "name": "acc",
        "platform": PricingPlatform.ML,
        "department": Department.CELULAR,
        "kit_number": 1,
        "commission": Decimal("0.10"),
        "margin1": Decimal("0.20"),
        "shipping1": Decimal("5.00"),
        "sort_order": 0,
    }
    base.update(kwargs)
    return PricingAccount(**base)


def _product(**kwargs) -> PricingProduct:
    base = {
        "id": uuid4(),
        "user_id": uuid4(),
        "sku": "SKU-1",
        "name": "X",
        "department": Department.CELULAR,
        "product_type": 2,
        "cost_kit1": Decimal("100.00"),
        "is_active": True,
        "in_catalog": False,
    }
    base.update(kwargs)
    return PricingProduct(**base)


def _override(**kwargs) -> PricingOverride:
    base = {
        "id": uuid4(),
        "user_id": uuid4(),
        "pricing_product_id": uuid4(),
        "pricing_account_id": uuid4(),
        "cell_status": CellStatus.AUTO,
    }
    base.update(kwargs)
    return PricingOverride(**base)


def test_basic_formula():
    # (cost + shipping) / (1 - commission - margin) = (100 + 5) / 0.7 = 150.00
    out = calculate(_account(), _product())
    assert out.source == "computed"
    assert out.price == Decimal("150.00")


def test_kit_resolves_correct_pair():
    a = _account(
        kit_number=2,
        margin2=Decimal("0.30"),
        shipping2=Decimal("10.00"),
    )
    p = _product(cost_kit2=Decimal("80.00"))
    # (80 + 10) / (1 - 0.10 - 0.30) = 90 / 0.6 = 150.00
    out = calculate(a, p)
    assert out.price == Decimal("150.00")


def test_kit_falls_back_to_kit1_when_kit_n_null():
    # Default kit2 cost null → fall back to kit1=100
    a = _account(
        kit_number=2,
        margin2=Decimal("0.20"),
        shipping2=Decimal("5.00"),
    )
    p = _product(cost_kit2=None, cost_kit1=Decimal("100.00"))
    out = calculate(a, p)
    assert out.price == Decimal("150.00")


def test_kit_falls_back_when_kit_n_zero():
    a = _account(
        kit_number=2,
        margin2=Decimal("0.20"),
        shipping2=Decimal("5.00"),
    )
    p = _product(cost_kit2=Decimal("0"), cost_kit1=Decimal("100.00"))
    out = calculate(a, p)
    assert out.price == Decimal("150.00")


def test_missing_commission_returns_missing_inputs():
    a = _account(commission=None)
    out = calculate(a, _product())
    assert out.source == "missing_inputs"
    assert out.price is None


def test_missing_margin_returns_missing_inputs():
    a = _account(margin1=None)
    out = calculate(a, _product())
    assert out.source == "missing_inputs"


def test_missing_shipping_treats_as_zero():
    a = _account(shipping1=None)
    # (100 + 0) / 0.7 = 142.857... → 142.86
    out = calculate(a, _product())
    assert out.price == Decimal("142.86")


def test_non_positive_denominator_fails():
    a = _account(commission=Decimal("0.50"), margin1=Decimal("0.50"))
    out = calculate(a, _product())
    assert out.source == "missing_inputs"
    assert out.detail and "denominator" in out.detail


def test_override_manual_wins_over_computed():
    o = _override(
        cell_status=CellStatus.MANUAL, price_override=Decimal("999.99")
    )
    out = calculate(_account(), _product(), o)
    assert out.source == "override"
    assert out.price == Decimal("999.99")


def test_override_locked_returns_locked_no_price_when_blank():
    o = _override(cell_status=CellStatus.LOCKED, price_override=None)
    out = calculate(_account(), _product(), o)
    assert out.source == "locked"
    assert out.price is None


def test_override_locked_with_price_returns_price():
    o = _override(
        cell_status=CellStatus.LOCKED, price_override=Decimal("123.45")
    )
    out = calculate(_account(), _product(), o)
    assert out.source == "locked"
    assert out.price == Decimal("123.45")


def test_override_disabled_returns_disabled():
    o = _override(cell_status=CellStatus.DISABLED, price_override=Decimal("999"))
    out = calculate(_account(), _product(), o)
    assert out.source == "disabled"
    assert out.price is None


def test_rounding_half_up():
    # cost=10, ship=0, comm=0, margin=0.04 → 10 / 0.96 = 10.4166... → 10.42
    a = _account(commission=Decimal("0"), margin1=Decimal("0.04"), shipping1=Decimal("0"))
    p = _product(cost_kit1=Decimal("10.00"))
    out = calculate(a, p)
    assert out.price == Decimal("10.42")
