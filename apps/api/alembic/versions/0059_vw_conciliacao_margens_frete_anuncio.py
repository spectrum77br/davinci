# ruff: noqa: E501, S608
"""expose evento_frete_anuncio in vw_conciliacao_margens_marketplace

Adds the `frete_anuncio` event (Shopee BR commission table fixed
component, computed in _fetch_shopee) as a dedicated column in the
view and rebuilds the materialized view.

The event is info-only: it is NOT summed into net_payout/net_estimated
because it is already embedded inside `service_fee`.

Revision ID: 0059_vw_conciliacao_margens_frete_anuncio
Revises: 0058_vw_conciliacao_margens_fix_segment_priority
Create Date: 2026-05-18
"""

import importlib.util
from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0059_vw_conciliacao_margens_frete_anuncio"
down_revision: str | None = "0058_vw_conciliacao_margens_fix_segment_priority"
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


def _previous_view_sql() -> str:
    # Build on top of 0058's view (consolidated pricing_match — segment
    # resolution joined with account selection). Reading from 0055 would
    # regress that fix.
    mod = _load_module(
        "0058_vw_conciliacao_margens_fix_segment_priority.py",
        "_davinci_0058_view",
    )
    return mod._view_sql()


def _replace_once(sql: str, old: str, new: str) -> str:
    if old not in sql:
        raise RuntimeError(f"view SQL anchor not found: {old[:120]}")
    return sql.replace(old, new, 1)


def _view_sql_with_frete_anuncio() -> str:
    sql = _previous_view_sql()
    # 1. event_totals CTE — add the new filter just before evento_net_estimated.
    sql = _replace_once(
        sql,
        "SUM(ea.amount) FILTER (WHERE ea.event_type = 'net_estimated') AS evento_net_estimated",
        "SUM(ea.amount) FILTER (WHERE ea.event_type = 'frete_anuncio') AS evento_frete_anuncio,\n"
        "        SUM(ea.amount) FILTER (WHERE ea.event_type = 'net_estimated') AS evento_net_estimated",
    )
    # 2. joined CTE projection — pull evento_frete_anuncio from et. The line
    #    after et.evento_net_estimated is `ft.marketplace_frete_reconciliacao_registros`
    #    (added by 0046), so anchor on that pair.
    sql = _replace_once(
        sql,
        "et.evento_net_estimated,\n        ft.marketplace_frete_reconciliacao_registros",
        "et.evento_frete_anuncio,\n        et.evento_net_estimated,\n        ft.marketplace_frete_reconciliacao_registros",
    )
    # 3. Top-level SELECT — surface j.evento_frete_anuncio in the output.
    sql = _replace_once(
        sql,
        "j.evento_net_estimated,\n    j.marketplace_frete_reconciliacao_registros",
        "j.evento_frete_anuncio,\n    j.evento_net_estimated,\n    j.marketplace_frete_reconciliacao_registros",
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
    # Inserting a new column in the middle of the SELECT means CREATE OR REPLACE
    # VIEW would reject it ("cannot change name of view column"). Drop both,
    # then recreate.
    op.execute(f'DROP MATERIALIZED VIEW IF EXISTS "{SCHEMA}"."{MV_NAME}"')
    op.execute(f'DROP VIEW IF EXISTS "{SCHEMA}"."{VIEW_NAME}"')
    op.execute(_view_sql_with_frete_anuncio())
    _recreate_mv()


def downgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.execute(f'DROP MATERIALIZED VIEW IF EXISTS "{SCHEMA}"."{MV_NAME}"')
    op.execute(f'DROP VIEW IF EXISTS "{SCHEMA}"."{VIEW_NAME}"')
    op.execute(_previous_view_sql())
    _recreate_mv()
