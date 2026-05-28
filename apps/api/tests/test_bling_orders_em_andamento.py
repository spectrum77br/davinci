"""Preservação de em_andamento_data no re-ingest de bling_orders.

Bug (regressão 2026-05-28): webhooks subsequentes de pedido já
despachado (taxa/endereço chegam dias depois) re-carimbavam
em_andamento_data com "hoje", empurrando pedidos antigos pro dia
atual. Fix: em_andamento_data é a data do PRIMEIRO "Em andamento"
e não muda — `_row_from_item` preserva o valor passado em
`preserved_em_andamento`.
"""
from __future__ import annotations

from datetime import UTC, date, datetime

from app.services.bling_orders import _row_from_item

_RAW_SHIPPED = {"situacao": {"id": 15}, "loja": {}, "itens": []}
_RAW_OPEN = {"situacao": {"id": 6}, "loja": {}, "itens": []}


def test_first_ship_stamps_today_when_no_preserved():
    """1ª vez em situacao=15 sem valor anterior → carimba hoje (BRT op)."""
    row = _row_from_item(_RAW_SHIPPED, {}, item_index=0, store_id=None)
    today_brt = _row_today()
    assert row["em_andamento_data"] == today_brt


def test_preserved_date_is_kept_on_reingest():
    """Webhook subsequente (situacao ainda 15) preserva a data original."""
    old = date(2026, 5, 26)
    row = _row_from_item(
        _RAW_SHIPPED, {}, item_index=0, store_id=None,
        preserved_em_andamento=old,
    )
    assert row["em_andamento_data"] == old


def test_open_status_without_preserved_is_none():
    row = _row_from_item(_RAW_OPEN, {}, item_index=0, store_id=None)
    assert row["em_andamento_data"] is None


def test_open_status_with_preserved_keeps_history():
    """Pedido que voltou de 15 → 6 mantém o ship-date histórico."""
    old = date(2026, 5, 26)
    row = _row_from_item(
        _RAW_OPEN, {}, item_index=0, store_id=None,
        preserved_em_andamento=old,
    )
    assert row["em_andamento_data"] == old


def _row_today() -> date:
    """Data operacional de hoje — espelha _operational_ship_date(now())
    sem reimportar (cutoff 10h BRT)."""
    from app.services.marketplace_shipment_check import _operational_ship_date
    return _operational_ship_date(datetime.now(UTC))
