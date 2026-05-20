"""refunds runtime defaults and indexes

Revision ID: 0069_refunds_runtime_defaults
Revises: 0068_integration_consecutive_errors
Create Date: 2026-05-20
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0069_refunds_runtime_defaults"
down_revision: str | None = "0068_integration_consecutive_errors"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute(
        f"""
        UPDATE {SCHEMA}.refunds
        SET
            conta = COALESCE(NULLIF(btrim(conta), ''), 'sem conta'),
            conferido = COALESCE(conferido, false),
            created_at = COALESCE(created_at, now()),
            updated_at = COALESCE(updated_at, now())
        """  # noqa: S608
    )
    op.execute(f"ALTER TABLE {SCHEMA}.refunds ALTER COLUMN id SET DEFAULT gen_random_uuid()")
    op.execute(f"ALTER TABLE {SCHEMA}.refunds ALTER COLUMN conta SET NOT NULL")
    op.execute(f"ALTER TABLE {SCHEMA}.refunds ALTER COLUMN conferido SET DEFAULT false")
    op.execute(f"ALTER TABLE {SCHEMA}.refunds ALTER COLUMN conferido SET NOT NULL")
    op.execute(f"ALTER TABLE {SCHEMA}.refunds ALTER COLUMN created_at SET DEFAULT now()")
    op.execute(f"ALTER TABLE {SCHEMA}.refunds ALTER COLUMN created_at SET NOT NULL")
    op.execute(f"ALTER TABLE {SCHEMA}.refunds ALTER COLUMN updated_at SET DEFAULT now()")
    op.execute(f"ALTER TABLE {SCHEMA}.refunds ALTER COLUMN updated_at SET NOT NULL")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'ck_refunds_conta_not_blank'
                  AND conrelid = '{SCHEMA}.refunds'::regclass
            ) THEN
                ALTER TABLE {SCHEMA}.refunds
                ADD CONSTRAINT ck_refunds_conta_not_blank CHECK (length(btrim(conta)) > 0);
            END IF;
        END $$;
        """  # noqa: S608
    )
    op.execute(f"CREATE INDEX IF NOT EXISTS ix_refunds_data ON {SCHEMA}.refunds (data)")
    op.execute(f"CREATE INDEX IF NOT EXISTS ix_refunds_conta ON {SCHEMA}.refunds (conta)")
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_refunds_pedido_bling "
        f"ON {SCHEMA}.refunds (pedido_bling)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_refunds_pedido_marketplace "
        f"ON {SCHEMA}.refunds (pedido_marketplace)"
    )
    op.execute(f"CREATE INDEX IF NOT EXISTS ix_refunds_conferido ON {SCHEMA}.refunds (conferido)")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regprocedure('{SCHEMA}.set_updated_at()') IS NOT NULL
               AND NOT EXISTS (
                   SELECT 1
                   FROM pg_trigger
                   WHERE tgname = 'trg_refunds_updated_at'
                     AND tgrelid = '{SCHEMA}.refunds'::regclass
               ) THEN
                CREATE TRIGGER trg_refunds_updated_at
                BEFORE UPDATE ON {SCHEMA}.refunds
                FOR EACH ROW EXECUTE FUNCTION {SCHEMA}.set_updated_at();
            END IF;
        END $$;
        """  # noqa: S608
    )
    op.execute(
        f"COMMENT ON COLUMN {SCHEMA}.refunds.conta IS "
        "'Mesmo valor da Conta exposta por vw_conciliacao_margens_marketplace.'"
    )


def downgrade() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS trg_refunds_updated_at ON {SCHEMA}.refunds")
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.ix_refunds_conferido")
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.ix_refunds_pedido_marketplace")
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.ix_refunds_pedido_bling")
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.ix_refunds_conta")
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.ix_refunds_data")
    op.execute(f"ALTER TABLE {SCHEMA}.refunds DROP CONSTRAINT IF EXISTS ck_refunds_conta_not_blank")
    op.execute(f"ALTER TABLE {SCHEMA}.refunds ALTER COLUMN updated_at DROP NOT NULL")
    op.execute(f"ALTER TABLE {SCHEMA}.refunds ALTER COLUMN updated_at DROP DEFAULT")
    op.execute(f"ALTER TABLE {SCHEMA}.refunds ALTER COLUMN created_at DROP NOT NULL")
    op.execute(f"ALTER TABLE {SCHEMA}.refunds ALTER COLUMN created_at DROP DEFAULT")
    op.execute(f"ALTER TABLE {SCHEMA}.refunds ALTER COLUMN conferido DROP NOT NULL")
    op.execute(f"ALTER TABLE {SCHEMA}.refunds ALTER COLUMN conferido DROP DEFAULT")
    op.execute(f"ALTER TABLE {SCHEMA}.refunds ALTER COLUMN conta DROP NOT NULL")
    op.execute(f"ALTER TABLE {SCHEMA}.refunds ALTER COLUMN id DROP DEFAULT")
