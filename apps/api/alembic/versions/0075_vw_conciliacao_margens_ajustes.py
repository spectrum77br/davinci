# ruff: noqa: E501, S608
"""add ajustes (refunds.prejuizo) and saldo_final to view + table

Two new columns at the end of vw_conciliacao_margens_marketplace:

- ajustes      = SUM(refunds.prejuizo) for the row's pedido_bling,
                 proportioned to the item by item_proportion (default 0).
- saldo_final  = saldo_efetivo (marketplace_liquido_base_margem_item) − ajustes.

`refunds.prejuizo` is double precision; we cast to numeric for the
arithmetic with item_proportion to stay consistent with the rest of
the view's numeric domain.

verificar_margem table is rebuilt to mirror the new view schema
(re-uses the same DROP/CREATE/backfill pattern as 0073/0074).

Revision ID: 0075_vw_conciliacao_margens_ajustes
Revises: 0074_vw_conciliacao_margens_bling_calc
Create Date: 2026-05-21
"""

import importlib.util
from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0075_vw_conciliacao_margens_ajustes"
down_revision: str | None = "0074_vw_conciliacao_margens_bling_calc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
VIEW = "vw_conciliacao_margens_marketplace"
TABLE = "verificar_margem"


def _load_module(filename: str, modname: str):
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(modname, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {filename}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _previous_view_sql() -> str:
    mod = _load_module(
        "0074_vw_conciliacao_margens_bling_calc.py",
        "_davinci_0074_view",
    )
    return mod._view_sql_with_bling_calc()


def _replace_once(sql: str, old: str, new: str) -> str:
    if old not in sql:
        raise RuntimeError(f"view SQL anchor not found: {old[:120]}")
    return sql.replace(old, new, 1)


AJUSTES_BLOCK = """,
        (COALESCE(ra.prejuizo_total, 0::numeric) * j.item_proportion) AS ajustes,
        (j.marketplace_liquido_base_margem_pedido * j.item_proportion - COALESCE(ra.prejuizo_total, 0::numeric) * j.item_proportion) AS saldo_final"""

REFUNDS_JOIN = """
LEFT JOIN LATERAL (
    SELECT COALESCE(SUM(r.prejuizo), 0::double precision)::numeric AS prejuizo_total
    FROM davinci.refunds r
    WHERE r.pedido_bling = j.pedido_bling
) ra ON TRUE"""


def _view_sql_with_ajustes() -> str:
    sql = _previous_view_sql()
    # Anchor includes both the last-projection line and the FROM clause so we
    # can append columns AND the LATERAL join in one replace.
    sql = _replace_once(
        sql,
        "END AS bling_margem_calculado\n\nFROM joined j\nLEFT JOIN pricing_match pm ON pm.bling_id = j.bling_id AND pm.item_codigo = j.sku",
        "END AS bling_margem_calculado"
        + AJUSTES_BLOCK
        + "\n\nFROM joined j"
        + "\nLEFT JOIN pricing_match pm ON pm.bling_id = j.bling_id AND pm.item_codigo = j.sku"
        + REFUNDS_JOIN,
    )
    return sql


def _recreate_table_from_view() -> None:
    op.execute(f'DROP TABLE IF EXISTS "{SCHEMA}"."{TABLE}"')
    op.execute(
        f'CREATE TABLE "{SCHEMA}"."{TABLE}" AS '
        f'SELECT * FROM "{SCHEMA}"."{VIEW}" WITH NO DATA'
    )
    op.execute(
        f'ALTER TABLE "{SCHEMA}"."{TABLE}" '
        f'ALTER COLUMN bling_order_item_id SET NOT NULL, '
        f'ADD CONSTRAINT {TABLE}_pkey PRIMARY KEY (bling_order_item_id)'
    )
    op.execute(
        f'CREATE INDEX "idx_{TABLE}_bling_id" ON "{SCHEMA}"."{TABLE}" (bling_id)'
    )
    op.execute(
        f'CREATE INDEX "idx_{TABLE}_data" ON "{SCHEMA}"."{TABLE}" (data DESC)'
    )
    op.execute(
        f'INSERT INTO "{SCHEMA}"."{TABLE}" SELECT * FROM "{SCHEMA}"."{VIEW}"'
    )


def upgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.execute(f'DROP VIEW IF EXISTS "{SCHEMA}"."{VIEW}" CASCADE')
    op.execute(_view_sql_with_ajustes())
    _recreate_table_from_view()


def downgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.execute(f'DROP VIEW IF EXISTS "{SCHEMA}"."{VIEW}" CASCADE')
    op.execute(_previous_view_sql())
    _recreate_table_from_view()
