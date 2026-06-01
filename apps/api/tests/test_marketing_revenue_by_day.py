"""Regression: ml_sync e amazon_sync DEVEM usar bling.by_day[today], não
bling.total da janela. Antes do fix gravavam `bling.total` em
MarketingMetric.revenue do row de hoje, inflando agregados 7d/30d ~2x.

Não vale rodar sync_ml_integration completo (custo de mock alto: MLAdsClient,
token refresh, daily performance, etc). Em vez disso este teste:
  1. valida o slice de cálculo (dict.get com fallback);
  2. checa o source dos 2 arquivos pra impedir regressão pra `bling.total`.
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from app.services.marketing.bling_revenue import BlingRevenue

_REPO = Path(__file__).resolve().parents[1]
_ML_SYNC = _REPO / "app/services/marketing/ml_sync.py"
_AMAZON_SYNC = _REPO / "app/services/marketing/amazon_sync.py"


def test_slice_usa_by_day_do_dia():
    """Total da janela é IGNORADO; só conta o dia atual."""
    today = date(2026, 5, 30)
    bling = BlingRevenue(
        total=10000.0,
        by_day={today: 500.0, date(2026, 5, 29): 9500.0},
        order_count=2,
    )
    bling_today = bling.by_day.get(today, 0.0) if bling else 0.0
    assert bling_today == 500.0
    assert bling_today != bling.total  # regredia se virasse 10000.0


def test_slice_dia_ausente_no_by_day_zera():
    """Sync rodando num dia sem faturamento Bling: revenue = 0.0
    (não cai no fallback do daily marketplace)."""
    today = date(2026, 5, 30)
    bling = BlingRevenue(
        total=1000.0,
        by_day={date(2026, 5, 29): 1000.0},  # ontem só
        order_count=1,
    )
    bling_today = bling.by_day.get(today, 0.0) if bling else 0.0
    assert bling_today == 0.0


def _account_revenue_assignment(source: str) -> str:
    """Extrai a linha `account_revenue = ...` (o que vai pro
    MarketingMetric.revenue). É AQUI que o bug morava."""
    m = re.search(r"^\s*account_revenue\s*=.*$", source, re.MULTILINE)
    assert m, "linha account_revenue não encontrada"
    return m.group(0)


def test_ml_sync_nao_usa_bling_total_no_revenue():
    """Bloqueia regressão no ml_sync — quem reverter pra `bling.total`
    em account_revenue quebra este teste."""
    src = _ML_SYNC.read_text(encoding="utf-8")
    line = _account_revenue_assignment(src)
    assert "bling_today" in line, f"esperava bling_today, achei: {line!r}"
    assert "bling.total" not in line, f"regressão pra bling.total: {line!r}"


def test_amazon_sync_nao_usa_bling_total_no_revenue():
    src = _AMAZON_SYNC.read_text(encoding="utf-8")
    line = _account_revenue_assignment(src)
    assert "bling_today" in line, f"esperava bling_today, achei: {line!r}"
    assert "bling.total" not in line, f"regressão pra bling.total: {line!r}"
