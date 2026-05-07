"""Pricing calc engine (Fase 9b).

Formula
-------
Given:
- account: pricing_account (commission, margin_K, shipping_K, kit_number)
- product: pricing_product (cost_kit_K, bling_cost_price)
- override: optional pricing_override

Resolution order (highest precedence first):
1. `override.cell_status == 'locked' or 'disabled'`  → no price (skip).
2. `override.price_override` set                     → use it as-is.
3. Compute from cost + commission + margin + shipping.

Compute formula:
    price = (cost + shipping) / (1 - commission - margin)

`cost` resolves to `product.cost_kit_{kit_number}` with fallback to
`cost_kit1` when the kit-N cost is NULL or 0 (defaults aprovados).
`commission` and `margin/shipping` of the matching kit slot must be set —
when missing, calc returns `None` and the caller treats the cell as
"not configured" (UI shows `—`, push refuses).

The result is a `Decimal` quantized to 2 decimals (cents-precision in BRL),
ROUND_HALF_UP — closer to seller intuition than banker's rounding.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models import PricingAccount, PricingOverride, PricingProduct


_TWO = Decimal("0.01")


@dataclass(slots=True)
class CalcOutcome:
    price: Decimal | None
    source: str  # "override" | "computed" | "locked" | "disabled" | "missing_inputs"
    detail: str | None = None
    inputs: dict | None = None


def _kit_value(product: PricingProduct, kit: int) -> Decimal | None:
    val = {
        1: product.cost_kit1,
        2: product.cost_kit2,
        3: product.cost_kit3,
        4: product.cost_kit4,
    }.get(kit)
    if val is None:
        return None
    d = Decimal(val) if not isinstance(val, Decimal) else val
    return d if d > 0 else None


def _account_pair(
    account: PricingAccount, kit: int
) -> tuple[Decimal | None, Decimal | None]:
    margins = {
        1: account.margin1,
        2: account.margin2,
        3: account.margin3,
        4: account.margin4,
        5: account.margin5,
    }
    shippings = {
        1: account.shipping1,
        2: account.shipping2,
        3: account.shipping3,
        4: account.shipping4,
        5: account.shipping5,
    }
    m = margins.get(kit)
    s = shippings.get(kit)
    return (
        Decimal(m) if m is not None else None,
        Decimal(s) if s is not None else None,
    )


def calculate(
    account: PricingAccount,
    product: PricingProduct,
    override: PricingOverride | None = None,
) -> CalcOutcome:
    if override is not None:
        raw = override.cell_status
        cs = raw.value if hasattr(raw, "value") else raw
        if cs == "disabled":
            return CalcOutcome(price=None, source="disabled")
        if cs == "locked":
            locked_price = (
                Decimal(override.price_override)
                if override.price_override is not None
                else None
            )
            return CalcOutcome(price=locked_price, source="locked")
        if override.price_override is not None:
            return CalcOutcome(
                price=Decimal(override.price_override).quantize(_TWO, ROUND_HALF_UP),
                source="override",
            )

    kit = int(account.kit_number or 1)
    cost = _kit_value(product, kit) or _kit_value(product, 1)
    margin, shipping = _account_pair(account, kit)
    commission = (
        Decimal(account.commission) if account.commission is not None else None
    )

    inputs = {
        "kit": kit,
        "cost": str(cost) if cost is not None else None,
        "commission": str(commission) if commission is not None else None,
        "margin": str(margin) if margin is not None else None,
        "shipping": str(shipping) if shipping is not None else None,
    }

    if cost is None or commission is None or margin is None:
        return CalcOutcome(
            price=None,
            source="missing_inputs",
            detail="cost/commission/margin required",
            inputs=inputs,
        )
    if shipping is None:
        shipping = Decimal("0")

    denom = Decimal("1") - commission - margin
    if denom <= 0:
        return CalcOutcome(
            price=None,
            source="missing_inputs",
            detail=f"non-positive denominator (commission+margin>=1): {denom}",
            inputs=inputs,
        )

    price = (cost + shipping) / denom
    return CalcOutcome(
        price=price.quantize(_TWO, ROUND_HALF_UP),
        source="computed",
        inputs=inputs,
    )
