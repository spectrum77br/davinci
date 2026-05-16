# ruff: noqa: E501, S608
"""shrink vw_conciliacao_margens_marketplace window 30d → 20d

The view's two `data >= now() - interval '30 days'` filters
(one in the `joined` CTE, one in `bp_keys`) are replaced with
20 days. Smaller window → cheaper MV refresh (~17s vs ~21s)
and the older bling orders rarely change after settlement.

Revision ID: 0055_vw_conciliacao_margens_20d_window
Revises: 0054_mv_conciliacao_margens_marketplace
Create Date: 2026-05-16
"""

import importlib.util
from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0055_vw_conciliacao_margens_20d_window"
down_revision: str | None = "0054_mv_conciliacao_margens_marketplace"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
VIEW_NAME = "vw_conciliacao_margens_marketplace"


def _load_module(filename: str, modname: str):
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(modname, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {filename}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _slot_match_view_sql() -> str:
    return str(_load_module(
        "0053_vw_conciliacao_margens_slot_match.py",
        "_davinci_0053_view",
    )._view_sql())


def upgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    sql = _slot_match_view_sql().replace("interval '30 days'", "interval '20 days'")
    if "interval '30 days'" in sql:
        raise RuntimeError("not all 30-day filters were replaced")
    op.execute(sql)
    # Drop and recreate MV so it picks up the shorter window.
    op.execute(f'DROP MATERIALIZED VIEW IF EXISTS "{SCHEMA}".mv_conciliacao_margens_marketplace')
    op.execute(
        f'CREATE MATERIALIZED VIEW "{SCHEMA}".mv_conciliacao_margens_marketplace AS '
        f'SELECT * FROM "{SCHEMA}".vw_conciliacao_margens_marketplace'
    )
    op.execute(
        f'CREATE UNIQUE INDEX "uq_mv_conciliacao_margens_marketplace_bling_order_item_id" '
        f'ON "{SCHEMA}".mv_conciliacao_margens_marketplace (bling_order_item_id)'
    )
    op.execute(
        f'CREATE INDEX "ix_mv_conciliacao_margens_marketplace_data_desc" '
        f'ON "{SCHEMA}".mv_conciliacao_margens_marketplace (data DESC NULLS LAST)'
    )
    op.execute(
        f'CREATE INDEX "ix_mv_conciliacao_margens_marketplace_plataforma" '
        f'ON "{SCHEMA}".mv_conciliacao_margens_marketplace (plataforma_bling)'
    )
    op.execute(
        f'CREATE INDEX "ix_mv_conciliacao_margens_marketplace_pedido_bling" '
        f'ON "{SCHEMA}".mv_conciliacao_margens_marketplace (pedido_bling)'
    )


def downgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.execute(_slot_match_view_sql())
    op.execute(f'DROP MATERIALIZED VIEW IF EXISTS "{SCHEMA}".mv_conciliacao_margens_marketplace')
    op.execute(
        f'CREATE MATERIALIZED VIEW "{SCHEMA}".mv_conciliacao_margens_marketplace AS '
        f'SELECT * FROM "{SCHEMA}".vw_conciliacao_margens_marketplace'
    )
    op.execute(
        f'CREATE UNIQUE INDEX "uq_mv_conciliacao_margens_marketplace_bling_order_item_id" '
        f'ON "{SCHEMA}".mv_conciliacao_margens_marketplace (bling_order_item_id)'
    )
    op.execute(
        f'CREATE INDEX "ix_mv_conciliacao_margens_marketplace_data_desc" '
        f'ON "{SCHEMA}".mv_conciliacao_margens_marketplace (data DESC NULLS LAST)'
    )
    op.execute(
        f'CREATE INDEX "ix_mv_conciliacao_margens_marketplace_plataforma" '
        f'ON "{SCHEMA}".mv_conciliacao_margens_marketplace (plataforma_bling)'
    )
    op.execute(
        f'CREATE INDEX "ix_mv_conciliacao_margens_marketplace_pedido_bling" '
        f'ON "{SCHEMA}".mv_conciliacao_margens_marketplace (pedido_bling)'
    )
