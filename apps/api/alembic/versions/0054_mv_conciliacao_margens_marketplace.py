# ruff: noqa: E501, S608
"""materialize vw_conciliacao_margens_marketplace as mv_*

The logical view is expensive (~20s for a single query) because it
materializes ~6.3k rows across multiple LATERAL joins and CTEs before
LIMIT can apply. Querying it from the UI causes 502 timeouts at the
proxy.

This creates an MV with the same SELECT (so column shape is identical)
plus a unique index for CONCURRENT refresh and supporting indexes for
the API's filter/sort path. The API queries the MV instead of the
view; refresh runs out-of-band (worker or scheduled task).

Revision ID: 0054_mv_conciliacao_margens_marketplace
Revises: 0053_vw_conciliacao_margens_slot_match
Create Date: 2026-05-16
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0054_mv_conciliacao_margens_marketplace"
down_revision: str | None = "0053_vw_conciliacao_margens_slot_match"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
MV_NAME = "mv_conciliacao_margens_marketplace"
VIEW_NAME = "vw_conciliacao_margens_marketplace"


def upgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.execute(f'DROP MATERIALIZED VIEW IF EXISTS "{SCHEMA}"."{MV_NAME}"')
    op.execute(
        f'CREATE MATERIALIZED VIEW "{SCHEMA}"."{MV_NAME}" AS '
        f'SELECT * FROM "{SCHEMA}"."{VIEW_NAME}"'
    )
    # bling_order_item_id is the natural PK of the underlying view.
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
        f"COMMENT ON MATERIALIZED VIEW {SCHEMA}.{MV_NAME} IS "
        "'Materializacao de vw_conciliacao_margens_marketplace. "
        "Refresh CONCURRENTLY apos sincronizacao financeira ou via cron.'"
    )


def downgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.execute(f'DROP MATERIALIZED VIEW IF EXISTS "{SCHEMA}"."{MV_NAME}"')
