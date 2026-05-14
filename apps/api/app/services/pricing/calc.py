"""Pricing calc engine (Fase 9b).

Formula
-------
Given:
- account: pricing_account (commission, margin_T, shipping_T, kit_number)
- product: pricing_product (cost_kit_K, product_type T)
- override: optional pricing_override

Resolution order (highest precedence first):
1. `override.cell_status == 'locked' or 'disabled'`  → no price (skip).
2. `override.price_override` set                     → use it as-is.
3. Compute from cost + commission + margin + shipping.

Compute formula (alinhada ao SSH):
    price = (cost * (1 + margin) + shipping) / (1 - commission)

Equivalent to the SSH wording:
    price = (Custo × Margem + Frete + Custo) / (1 - Comissão)

Slot mapping (SSH semantics):
  * `cost` = `product.cost_kit_{account.kit_number}`, fallback `cost_kit1`.
  * `margin/shipping` = `account.margin_{product.product_type}` /
    `account.shipping_{product.product_type}`. The product type (1..5)
    is the leaf-segment column under the root department.

`commission` and the matching margin slot must both be set — when missing,
calc returns `None` and the caller treats the cell as "not configured"
(UI shows `—`/NA, push refuses).

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
_ONE = Decimal("1")


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
    account: PricingAccount, slot: int
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
    m = margins.get(slot)
    s = shippings.get(slot)
    return (
        Decimal(m) if m is not None else None,
        Decimal(s) if s is not None else None,
    )


def calculate(
    account: PricingAccount,
    product: PricingProduct,
    override: PricingOverride | None = None,
    product_type: int | None = None,
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
    # Margin/shipping slot is the product type (Acessórios/Diversos/Regular/
    # Robusto/Apple). When the caller didn't resolve it, fall back to a
    # transient attr the API sets after the segment lookup; ultimate fallback
    # is the account's kit_number (legacy behavior).
    if product_type is None:
        product_type = getattr(product, "_product_type", None) or kit
    slot = max(1, min(5, int(product_type)))
    cost = _kit_value(product, kit) or _kit_value(product, 1)
    margin, shipping = _account_pair(account, slot)
    commission = (
        Decimal(account.commission) if account.commission is not None else None
    )

    inputs = {
        "kit": kit,
        "product_type": slot,
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

    denom = Decimal("1") - commission
    if denom <= 0:
        return CalcOutcome(
            price=None,
            source="missing_inputs",
            detail=f"non-positive denominator (commission>=1): {denom}",
            inputs=inputs,
        )

    # SSH formula: Math.round((cost * (1+margin) + shipping) / (1-commission))
    # SSH rounds to integer reais — the computed price is *always* a whole
    # number; only manual overrides may carry cents.
    price = (cost * (_ONE + margin) + shipping) / denom
    return CalcOutcome(
        price=price.quantize(_ONE, ROUND_HALF_UP),
        source="computed",
        inputs=inputs,
    )
