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
    `account.shipping_{product.product_type}`. The product type (1..10 —
    M-93, services/pricing/posicoes.MAX_CATEGORIAS_VENDA; antes 1..5) is the
    leaf-segment column under the root department. Margem vazia na posição
    (ex.: categoria nova, que nasce com "—" em todas as contas) = sem preço,
    como qualquer célula sem margem: nenhum valor padrão é inventado.
  * Sem o tipo resolvido (caminho antigo: o tipo vira o kit da conta), o tipo
    continua preso em 1..5 como antes da M-93 — nenhum preço já calculado muda.

`commission` and the matching margin slot must both be set — when missing,
calc returns `None` and the caller treats the cell as "not configured"
(UI shows `—`/NA, push refuses).

Catálogo ML (06/10/2026): a conta com `canal == 'catalogo'` (coluna de
catálogo, "filha" de uma conta ML de kit) usa como custo o
`product.preco_catalogo` e os outros números da conta base (`conta_base`:
comissão, frete do tipo, kit_number). Sem preço de catálogo (NULL ou 0) →
`missing_inputs` com detail "sem_preco_catalogo" — nunca cai para o Kit 1.

Margem própria do catálogo (07/10/2026): a margem do tipo T é a
`margin{T}` da própria filha quando preenchida; NULL = a da base. Comissão
e frete continuam sempre os da base.

The result is a `Decimal` quantized to 2 decimals (cents-precision in BRL),
ROUND_HALF_UP — closer to seller intuition than banker's rounding.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING

from app.services.pricing.posicoes import MAX_CATEGORIAS_VENDA, POSICOES_ANTES_DA_M93

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
        5: product.cost_kit5,
        6: product.cost_kit6,
        7: product.cost_kit7,
        8: product.cost_kit8,
    }.get(kit)
    if val is None:
        return None
    d = Decimal(val) if not isinstance(val, Decimal) else val
    return d if d > 0 else None


def _account_pair(
    account: PricingAccount, slot: int
) -> tuple[Decimal | None, Decimal | None]:
    """(margin{slot}, shipping{slot}) da conta; posição fora de 1..10 = (None, None)."""
    if not 1 <= slot <= MAX_CATEGORIAS_VENDA:
        return None, None
    m = getattr(account, f"margin{slot}", None)
    s = getattr(account, f"shipping{slot}", None)
    return (
        Decimal(m) if m is not None else None,
        Decimal(s) if s is not None else None,
    )


def _preco_catalogo(product: PricingProduct) -> Decimal | None:
    val = getattr(product, "preco_catalogo", None)
    if val is None:
        return None
    d = Decimal(val) if not isinstance(val, Decimal) else val
    return d if d > 0 else None


def calculate(
    account: PricingAccount,
    product: PricingProduct,
    override: PricingOverride | None = None,
    product_type: int | None = None,
    *,
    conta_base: PricingAccount | None = None,
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

    catalogo = (getattr(account, "canal", None) or "kit") == "catalogo"
    # Coluna de catálogo: os números são os da conta base; só a margem pode
    # ser da própria filha (por tipo, quando preenchida).
    params = account
    if catalogo:
        if conta_base is None:
            return CalcOutcome(
                price=None,
                source="missing_inputs",
                detail="sem_conta_base",
                inputs={"canal": "catalogo"},
            )
        params = conta_base

    kit = int(params.kit_number or 1)
    # Margin/shipping slot is the product type (Acessórios/Diversos/Regular/
    # Robusto/Apple…). When the caller didn't resolve it, fall back to a
    # transient attr the API sets after the segment lookup; ultimate fallback
    # is the account's kit_number (legacy behavior).
    teto = MAX_CATEGORIAS_VENDA
    if product_type is None:
        product_type = getattr(product, "_product_type", None)
        if not product_type:
            # Caminho antigo (o kit no lugar do tipo, ex.: services/audit/runner):
            # preso em 1..5 como antes da M-93, para nenhum preço já calculado mudar.
            product_type, teto = kit, POSICOES_ANTES_DA_M93
    slot = max(1, min(teto, int(product_type)))
    if catalogo:
        cost = _preco_catalogo(product)
    else:
        cost = _kit_value(product, kit) or _kit_value(product, 1)
    margin, shipping = _account_pair(params, slot)
    margem_propria = None
    if catalogo:
        margem_propria, _ = _account_pair(account, slot)
        if margem_propria is not None:
            margin = margem_propria
    commission = (
        Decimal(params.commission) if params.commission is not None else None
    )

    inputs = {
        "kit": kit,
        "product_type": slot,
        "cost": str(cost) if cost is not None else None,
        "commission": str(commission) if commission is not None else None,
        "margin": str(margin) if margin is not None else None,
        "shipping": str(shipping) if shipping is not None else None,
    }
    if catalogo:
        inputs["canal"] = "catalogo"
        inputs["margem_propria"] = margem_propria is not None
        if cost is None:
            return CalcOutcome(
                price=None,
                source="missing_inputs",
                detail="sem_preco_catalogo",
                inputs=inputs,
            )

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
