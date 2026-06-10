"""`_ml_free_shipping_quote` não re-aplica o desconto sobre `list_cost`.

Bug (2026-06-10): o `list_cost` retornado por /shipping_options/free já é
o valor líquido que o vendedor paga (o payload traz
`discount.promoted_amount` = preço cheio). O parser aplicava
`list_cost * (1 - rate)` em cima, descontando duas vezes — ex.: pedido ML
2000016853024850 ficava com frete_anuncio 11,82 quando o frete real
(senders[0].cost e a tela da venda) era 23,65. Em cascata, o auto-insert
de refunds tipo='Logistica' registrava prejuízo falso de 11,83.

O `promised` deve ser o próprio `list_cost`; o `rate` segue retornado
apenas como informativo (armazenado em freight_discount_rate).
"""
from __future__ import annotations

from decimal import Decimal

from app.services.marketplace_financials import _ml_free_shipping_quote


def _payload(list_cost: float, rate: float | None) -> dict:
    all_country: dict = {"list_cost": list_cost, "currency_id": "BRL"}
    if rate is not None:
        # Espelha o shape real: promoted_amount é o preço CHEIO, list_cost
        # já vem descontado.
        all_country["discount"] = {
            "rate": rate,
            "type": "mandatory",
            "promoted_amount": list_cost / (1 - rate) if rate < 1 else list_cost,
        }
    return {"coverage": {"all_country": all_country}}


def test_promised_equals_list_cost_with_mandatory_discount():
    # Caso real do pedido 2000016853024850: list_cost 23.65, rate 0.5.
    list_cost, rate, promised = _ml_free_shipping_quote(_payload(23.65, 0.5))
    assert list_cost == Decimal("23.65")
    assert rate == Decimal("0.5")
    assert promised == Decimal("23.65")


def test_promised_equals_list_cost_without_discount():
    list_cost, rate, promised = _ml_free_shipping_quote(_payload(18.90, None))
    assert list_cost == Decimal("18.90")
    assert rate is None
    assert promised == Decimal("18.90")


def test_missing_coverage_returns_nones():
    assert _ml_free_shipping_quote({}) == (None, None, None)
    assert _ml_free_shipping_quote(None) == (None, None, None)


def test_promised_line_total_multiplies_by_quantity():
    # Caso real do pedido 2000016849422942: cotação 6,75/un, qty 2 ->
    # ML cobrou 13,50. Sem a multiplicação o pedido parecia ter prejuízo.
    from app.services.marketplace_financials import _ml_promised_line_total

    assert _ml_promised_line_total(Decimal("6.75"), Decimal("2")) == Decimal("13.50")
    assert _ml_promised_line_total(Decimal("23.65"), Decimal("1")) == Decimal("23.65")
    assert _ml_promised_line_total(Decimal("10"), None) == Decimal("10")
    assert _ml_promised_line_total(Decimal("10"), Decimal("0")) == Decimal("10")
    assert _ml_promised_line_total(None, Decimal("3")) is None
