# ruff: noqa: E501, S608
"""COALESCE evento_freight to 0 when financial record exists

When Shopee fully subsidizes shipping (rebate == actual), `final_shipping_fee`
is 0 → `_event("freight", ...)` returns None (since `_event` skips zero
amounts) → no `freight` row in marketplace_financial_events → the view's
`SUM(...) FILTER (WHERE event_type='freight')` returns NULL.

This makes "frete grátis líquido" indistinguishable from "data not synced
yet" in the UI. We force the value to 0 when a financial record exists
(financeiro_id IS NOT NULL) and keep NULL when there is no financial at
all (unsynced order).

Revision ID: 0060_vw_conciliacao_margens_freight_coalesce
Revises: 0059_vw_conciliacao_margens_frete_anuncio
Create Date: 2026-05-18
"""

import importlib.util
from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0060_vw_conciliacao_margens_freight_coalesce"
down_revision: str | None = "0059_vw_conciliacao_margens_frete_anuncio"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
VIEW_NAME = "vw_conciliacao_margens_marketplace"
MV_NAME = "mv_conciliacao_margens_marketplace"


def _load_module(filename: str, modname: str):
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(modname, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {filename}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _replace_once(sql: str, old: str, new: str) -> str:
    if old not in sql:
        raise RuntimeError(f"view SQL anchor not found: {old[:120]}")
    return sql.replace(old, new, 1)


def _base_view_sql() -> str:
    """Build the post-0059 view SQL (with evento_frete_anuncio on top of
    0058's consolidated pricing_match)."""
    mod = _load_module(
        "0059_vw_conciliacao_margens_frete_anuncio.py",
        "_davinci_0059_view",
    )
    return mod._view_sql_with_frete_anuncio()


def _view_sql_with_coalesced_freight() -> str:
    sql = _base_view_sql()
    # Wrap `j.evento_freight,` in the top-level SELECT with CASE so it shows 0
    # instead of NULL when a financial record exists. The CTE-level field
    # (et.evento_freight) is left untouched — propagation through `joined` is
    # automatic via SELECT * FROM joined.
    sql = _replace_once(
        sql,
        "    j.evento_freight,\n    j.evento_shipping_rebate,",
        "    CASE WHEN j.financeiro_id IS NOT NULL THEN COALESCE(j.evento_freight, 0) ELSE j.evento_freight END AS evento_freight,\n"
        "    j.evento_shipping_rebate,",
    )
    return sql


def _recreate_mv() -> None:
    op.execute(f'DROP MATERIALIZED VIEW IF EXISTS "{SCHEMA}"."{MV_NAME}"')
    op.execute(
        f'CREATE MATERIALIZED VIEW "{SCHEMA}"."{MV_NAME}" AS '
        f'SELECT * FROM "{SCHEMA}".{VIEW_NAME}'
    )
    op.execute(
        f'CREATE UNIQUE INDEX "uq_{MV_NAME}_bling_order_item_id" '
        f'ON "{SCHEMA}"."{MV_NAME}" (bling_order_item_id)'
    )
    op.execute(
        f'CREATE INDEX "ix_{MV_NAME}_data_desc" '
        f'ON "{SCHEMA}"."{MV_NAME}" (data DESC NULLS LAST)'
    )
    op.execute(
        f'CREATE INDEX "ix_{MV_NAME}_plataforma" '
        f'ON "{SCHEMA}"."{MV_NAME}" (plataforma_bling)'
    )
    op.execute(
        f'CREATE INDEX "ix_{MV_NAME}_pedido_bling" '
        f'ON "{SCHEMA}"."{MV_NAME}" (pedido_bling)'
    )
    op.execute(
        f'CREATE INDEX "ix_{MV_NAME}_status" '
        f'ON "{SCHEMA}"."{MV_NAME}" (bling_status_margem)'
    )


def upgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    # CASE expression renames the output column type, so `CREATE OR REPLACE
    # VIEW` may reject it — drop both and recreate.
    op.execute(f'DROP MATERIALIZED VIEW IF EXISTS "{SCHEMA}"."{MV_NAME}"')
    op.execute(f'DROP VIEW IF EXISTS "{SCHEMA}"."{VIEW_NAME}"')
    op.execute(_view_sql_with_coalesced_freight())
    _recreate_mv()


def downgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.execute(f'DROP MATERIALIZED VIEW IF EXISTS "{SCHEMA}"."{MV_NAME}"')
    op.execute(f'DROP VIEW IF EXISTS "{SCHEMA}"."{VIEW_NAME}"')
    op.execute(_base_view_sql())
    _recreate_mv()
