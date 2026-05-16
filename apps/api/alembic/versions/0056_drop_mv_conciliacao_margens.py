# ruff: noqa: E501, S608
"""drop mv_conciliacao_margens_marketplace

The MV approach was reverted. Queries now hit the live view directly
so each pagination returns fresh data. The MV adds maintenance cost
(refresh, staleness window) for no payoff under the new requirement.

Revision ID: 0056_drop_mv_conciliacao_margens
Revises: 0055_vw_conciliacao_margens_20d_window
Create Date: 2026-05-16
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0056_drop_mv_conciliacao_margens"
down_revision: str | None = "0055_vw_conciliacao_margens_20d_window"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute(f'DROP MATERIALIZED VIEW IF EXISTS "{SCHEMA}".mv_conciliacao_margens_marketplace')


def downgrade() -> None:
    op.execute(
        f'CREATE MATERIALIZED VIEW IF NOT EXISTS "{SCHEMA}".mv_conciliacao_margens_marketplace AS '
        f'SELECT * FROM "{SCHEMA}".vw_conciliacao_margens_marketplace'
    )
    op.execute(
        f'CREATE UNIQUE INDEX IF NOT EXISTS "uq_mv_conciliacao_margens_marketplace_bling_order_item_id" '
        f'ON "{SCHEMA}".mv_conciliacao_margens_marketplace (bling_order_item_id)'
    )
    op.execute(
        f'CREATE INDEX IF NOT EXISTS "ix_mv_conciliacao_margens_marketplace_data_desc" '
        f'ON "{SCHEMA}".mv_conciliacao_margens_marketplace (data DESC NULLS LAST)'
    )
    op.execute(
        f'CREATE INDEX IF NOT EXISTS "ix_mv_conciliacao_margens_marketplace_plataforma" '
        f'ON "{SCHEMA}".mv_conciliacao_margens_marketplace (plataforma_bling)'
    )
    op.execute(
        f'CREATE INDEX IF NOT EXISTS "ix_mv_conciliacao_margens_marketplace_pedido_bling" '
        f'ON "{SCHEMA}".mv_conciliacao_margens_marketplace (pedido_bling)'
    )
