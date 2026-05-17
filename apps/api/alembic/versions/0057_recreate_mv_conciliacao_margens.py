# ruff: noqa: E501, S608
"""recreate mv_conciliacao_margens_marketplace

Brings back the materialized view dropped in 0056. The MV is fed
from the live view (20-day window). Refresh is driven entirely on
demand: API side-effects after PATCH status/observacao and the
'atualizar' button on /margem. No cron.

Revision ID: 0057_recreate_mv_conciliacao_margens
Revises: 0056_drop_mv_conciliacao_margens
Create Date: 2026-05-16
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0057_recreate_mv_conciliacao_margens"
down_revision: str | None = "0056_drop_mv_conciliacao_margens"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
MV_NAME = "mv_conciliacao_margens_marketplace"


def upgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.execute(f'DROP MATERIALIZED VIEW IF EXISTS "{SCHEMA}"."{MV_NAME}"')
    op.execute(
        f'CREATE MATERIALIZED VIEW "{SCHEMA}"."{MV_NAME}" AS '
        f'SELECT * FROM "{SCHEMA}".vw_conciliacao_margens_marketplace'
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
    op.execute(
        f"COMMENT ON MATERIALIZED VIEW {SCHEMA}.{MV_NAME} IS "
        "'On-demand cache of vw_conciliacao_margens_marketplace. "
        "Refreshed by the API after PATCH status/observacao and by "
        "the UIs atualizar button.'"
    )


def downgrade() -> None:
    op.execute(f'DROP MATERIALIZED VIEW IF EXISTS "{SCHEMA}"."{MV_NAME}"')
