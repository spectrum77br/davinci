# ruff: noqa: E501, S608
"""create davinci.verificar_margem + drop mv_conciliacao_margens_marketplace

The materialized view was the source for /api/margens/marketplace. It was
replaced by davinci.verificar_margem (snapshot table populated by the
worker cron `verificar_margem_snapshot` every 30 min and rebuilt on the
'atualizar' UI button). With the MV no longer read by the app this
migration formalises the swap:

- create the table (idempotent — already exists in prod from manual setup)
- drop the MV and its indexes

Revision ID: 0073_verificar_margem_table_drop_mv
Revises: 0072_refunds_frete_auto
Create Date: 2026-05-21
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0073_verificar_margem_table_drop_mv"
down_revision: str | None = "0072_refunds_frete_auto"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
TABLE = "verificar_margem"
MV = "mv_conciliacao_margens_marketplace"
VIEW = "vw_conciliacao_margens_marketplace"


def upgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')

    # Snapshot table — same columns/types as the view, PK on bling_order_item_id.
    # IF NOT EXISTS because prod already has it (created manually before this migration).
    op.execute(
        f'CREATE TABLE IF NOT EXISTS "{SCHEMA}"."{TABLE}" AS '
        f'SELECT * FROM "{SCHEMA}"."{VIEW}" WITH NO DATA'
    )
    # Constraints/indexes guarded so re-running on prod is a no-op.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.table_constraints
                WHERE table_schema = '{SCHEMA}'
                  AND table_name = '{TABLE}'
                  AND constraint_name = '{TABLE}_pkey'
            ) THEN
                EXECUTE 'ALTER TABLE "{SCHEMA}"."{TABLE}" '
                        'ALTER COLUMN bling_order_item_id SET NOT NULL, '
                        'ADD CONSTRAINT {TABLE}_pkey PRIMARY KEY (bling_order_item_id)';
            END IF;
        END $$;
        """
    )
    op.execute(
        f'CREATE INDEX IF NOT EXISTS "idx_{TABLE}_bling_id" '
        f'ON "{SCHEMA}"."{TABLE}" (bling_id)'
    )
    op.execute(
        f'CREATE INDEX IF NOT EXISTS "idx_{TABLE}_data" '
        f'ON "{SCHEMA}"."{TABLE}" (data DESC)'
    )

    # MV no longer used by the app — drop it.
    op.execute(f'DROP MATERIALIZED VIEW IF EXISTS "{SCHEMA}"."{MV}"')


def downgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')

    op.execute(
        f'CREATE MATERIALIZED VIEW IF NOT EXISTS "{SCHEMA}"."{MV}" AS '
        f'SELECT * FROM "{SCHEMA}"."{VIEW}"'
    )
    op.execute(
        f'CREATE UNIQUE INDEX IF NOT EXISTS "uq_{MV}_bling_order_item_id" '
        f'ON "{SCHEMA}"."{MV}" (bling_order_item_id)'
    )
    op.execute(
        f'CREATE INDEX IF NOT EXISTS "ix_{MV}_data_desc" '
        f'ON "{SCHEMA}"."{MV}" (data DESC NULLS LAST)'
    )
    op.execute(
        f'CREATE INDEX IF NOT EXISTS "ix_{MV}_plataforma" '
        f'ON "{SCHEMA}"."{MV}" (plataforma_bling)'
    )
    op.execute(
        f'CREATE INDEX IF NOT EXISTS "ix_{MV}_pedido_bling" '
        f'ON "{SCHEMA}"."{MV}" (pedido_bling)'
    )

    op.execute(f'DROP TABLE IF EXISTS "{SCHEMA}"."{TABLE}"')
