# ruff: noqa: E501, S608
"""add bling_lucro_calculado / bling_margem_calculado to view + table

Mirrors the marketplace_margem formula but using Bling source values:
- bling_lucro_calculado    = (bling_valorbase_item − bling_custofrete_item − bling_taxacomissao_item) − bling_custo_produtos
- bling_margem_calculado   = bling_lucro_calculado / bling_custo_produtos

The original `bling_margem` (from bp.margem upstream) and the
`marketplace_margem` are kept unchanged. The two new columns are
appended at the end of the view's projection so the verificar_margem
snapshot table can be recreated with the same column order
(`INSERT … SELECT *` in the cron stays correct).

Revision ID: 0074_vw_conciliacao_margens_bling_calc
Revises: 0073_verificar_margem_table_drop_mv
Create Date: 2026-05-21
"""

import importlib.util
from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0074_vw_conciliacao_margens_bling_calc"
down_revision: str | None = "0073_verificar_margem_table_drop_mv"
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
    """Latest view definition before this migration (0060 head)."""
    mod = _load_module(
        "0060_vw_conciliacao_margens_freight_coalesce.py",
        "_davinci_0060_view",
    )
    return mod._view_sql_with_coalesced_freight()


def _replace_once(sql: str, old: str, new: str) -> str:
    if old not in sql:
        raise RuntimeError(f"view SQL anchor not found: {old[:120]}")
    return sql.replace(old, new, 1)


BLING_CALC_BLOCK = """,
        CASE
            WHEN j.bling_valorbase_item IS NOT NULL AND COALESCE(j.bling_custo_produtos, 0::numeric) > 0::numeric
            THEN (j.bling_valorbase_item - COALESCE(j.bling_custofrete_item, 0::numeric) - COALESCE(j.bling_taxacomissao_item, 0::numeric)) - j.bling_custo_produtos
            ELSE NULL::numeric
        END AS bling_lucro_calculado,
        CASE
            WHEN j.bling_valorbase_item IS NOT NULL AND COALESCE(j.bling_custo_produtos, 0::numeric) > 0::numeric
            THEN ((j.bling_valorbase_item - COALESCE(j.bling_custofrete_item, 0::numeric) - COALESCE(j.bling_taxacomissao_item, 0::numeric)) - j.bling_custo_produtos) / j.bling_custo_produtos
            ELSE NULL::numeric
        END AS bling_margem_calculado"""


def _view_sql_with_bling_calc() -> str:
    sql = _previous_view_sql()
    sql = _replace_once(
        sql,
        "END AS frete_resultado_item\n\nFROM joined j",
        "END AS frete_resultado_item" + BLING_CALC_BLOCK + "\n\nFROM joined j",
    )
    return sql


def _recreate_table_from_view() -> None:
    """Drop and rebuild davinci.verificar_margem to match the new view schema.

    The cron `INSERT INTO verificar_margem SELECT * FROM view` requires
    matching column order/types — recreate the table from the view so the
    structure is in sync, then re-populate.
    """
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
    # Initial backfill from the live view.
    op.execute(
        f'INSERT INTO "{SCHEMA}"."{TABLE}" SELECT * FROM "{SCHEMA}"."{VIEW}"'
    )


def upgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.execute(f'DROP VIEW IF EXISTS "{SCHEMA}"."{VIEW}" CASCADE')
    op.execute(_view_sql_with_bling_calc())
    _recreate_table_from_view()


def downgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.execute(f'DROP VIEW IF EXISTS "{SCHEMA}"."{VIEW}" CASCADE')
    op.execute(_previous_view_sql())
    _recreate_table_from_view()
