"""Fórmula `previsto_brl = usd * (1 + frete_pct) * câmbio + adicional`.

Casos validados pelo operador 2026-06-02 — espelham os exemplos no
ticket da etapa 3.
"""
from __future__ import annotations

from decimal import Decimal

from app.services.importacao_cotacao import previsto_brl

# Defaults dos parâmetros (mesmo seed da migration 0119).
_DEFAULTS = {
    "taxa_cambio": Decimal("5.10"),
    "frete_regular_pct": Decimal("0.16"),
    "frete_swap_pct": Decimal("0.06"),
    "frete_acessorios_pct": Decimal("0.20"),
    "adicional": Decimal("12.00"),
}


def test_ipad_frete_regular():
    """iPad USD 340, frete regular: 340 * 1.16 * 5.10 + 12 = 2023.44."""
    r = previsto_brl(valor_usd=Decimal("340"), frete_type="regular", **_DEFAULTS)
    assert r is not None
    assert r == Decimal("2023.44")


def test_iphone17pro_frete_swap():
    """iPhone 17 Pro USD 1265, frete swap: 1265 * 1.06 * 5.10 + 12 = 6850.59."""
    r = previsto_brl(valor_usd=Decimal("1265"), frete_type="swap", **_DEFAULTS)
    assert r is not None
    assert r == Decimal("6850.59")


def test_acessorio_frete_acessorios():
    """Acessório USD 10, frete acessórios: 10 * 1.20 * 5.10 + 12 = 73.20."""
    r = previsto_brl(valor_usd=Decimal("10"), frete_type="acessorios", **_DEFAULTS)
    assert r is not None
    assert r == Decimal("73.20")


def test_valor_usd_none_retorna_none():
    assert previsto_brl(valor_usd=None, frete_type="regular", **_DEFAULTS) is None


def test_valor_usd_zero_ou_negativo_retorna_none():
    assert previsto_brl(valor_usd=Decimal("0"), frete_type="regular", **_DEFAULTS) is None
    assert previsto_brl(valor_usd=Decimal("-1"), frete_type="regular", **_DEFAULTS) is None


def test_taxa_e_adicional_mudados_recalcula():
    """Câmbio e adicional editáveis pelo operador alteram o resultado."""
    params = {**_DEFAULTS, "taxa_cambio": Decimal("6.00"), "adicional": Decimal("0")}
    # 340 * 1.16 * 6.00 = 2366.40
    r = previsto_brl(valor_usd=Decimal("340"), frete_type="regular", **params)
    assert r == Decimal("2366.40")
