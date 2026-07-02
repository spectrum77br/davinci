"""`_ml_seller_funded_discount` só desconta cupons — nunca *ofertas*.

Bug (2026-07-02): a exclusão de descontos "já embutidos no preço" era feita
apenas por `supplier.funding_mode == "sale_fee"`. Uma OFERTA de campanha com
`funding_mode == "seller"` (ex.: pedido 285633, `OFFER-MLB6955914024`, R$30,00)
escapava e era subtraída de novo sobre o `total_amount` (que já é o preço com a
oferta), derrubando a Plataforma de 155,47 para 125,47. A tela de repasse do ML
e o billing detail (`sale_fee.discount == 0`) confirmam que a oferta NÃO é
descontada no repasse.

A regra correta: qualquer detalhe com `offer_id` (independente do funding_mode)
é oferta embutida no `unit_price`/comissão → ignora. Só cupons (sem `offer_id`)
custam do vendedor a parcela `amounts.seller`.
"""
from __future__ import annotations

from decimal import Decimal

from app.services.marketplace_financials import _ml_seller_funded_discount


def _discounts(*details: dict) -> dict:
    return {"details": list(details)}


def _detail(seller: float, *, funding_mode=None, offer_id=None, total=None) -> dict:
    supplier: dict = {}
    if funding_mode is not None:
        supplier["funding_mode"] = funding_mode
    if offer_id is not None:
        supplier["offer_id"] = offer_id
    return {
        "type": "discount",
        "items": [{"amounts": {"total": total if total is not None else seller,
                               "seller": seller}, "quantity": 1}],
        "supplier": supplier,
    }


def test_seller_funded_offer_is_not_deducted():
    # Pedido 285633: oferta funding_mode="seller", offer_id presente. Skip.
    d = _discounts(_detail(30.0, funding_mode="seller", offer_id="OFFER-MLB6955914024-13213249556"))
    assert _ml_seller_funded_discount(d) == Decimal("0")


def test_sale_fee_offer_is_not_deducted():
    # Ofertas sale_fee sempre foram ignoradas — segue ignorando.
    d = _discounts(_detail(37.99, funding_mode="sale_fee", offer_id="OFFER-MLB6654257934-13014252554"))
    assert _ml_seller_funded_discount(d) == Decimal("0")


def test_non_offer_coupon_seller_share_is_deducted():
    # Pedido 282077: cupom sem offer_id, seller=2,00 → desconta.
    d = _discounts(
        _detail(2.0),  # coupon seller share (funding_mode=None, no offer_id)
        _detail(0.0, total=55.76),  # ML-funded coupon portion (seller=0)
        _detail(37.99, funding_mode="sale_fee", offer_id="OFFER-MLB6654257934-13014252554"),
    )
    assert _ml_seller_funded_discount(d) == Decimal("2.0")


def test_offer_plus_coupon_keeps_only_coupon():
    # Pedido 279881-like: oferta 179,10 (seller) + cupom 2,00 → só 2,00.
    d = _discounts(
        _detail(179.10, funding_mode="seller", offer_id="OFFER-X-1"),
        _detail(2.0),
    )
    assert _ml_seller_funded_discount(d) == Decimal("2.0")


def test_ml_funded_coupon_contributes_zero():
    # Pedido 282055: cupom 69,90 totalmente ML-funded (seller=0) → 0.
    d = _discounts(
        _detail(0.0, total=69.9),
        _detail(37.99, funding_mode="sale_fee", offer_id="OFFER-MLB6654257934-13014252554"),
    )
    assert _ml_seller_funded_discount(d) == Decimal("0")


def test_missing_or_empty_breakdown_returns_none():
    assert _ml_seller_funded_discount(None) is None
    assert _ml_seller_funded_discount({}) is None
    assert _ml_seller_funded_discount({"details": []}) is None
    assert _ml_seller_funded_discount({"details": "nope"}) is None
