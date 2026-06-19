"""marketing_commands outbox + automatic schedule columns

Adds the durable command queue that wires the Marketing module's
pause/resume/budget actions to the live Shopee/ML Ads APIs, plus the
per-account automatic-agenda fields:

- davinci.marketing_commands: outbox row per action (manual or schedule).
  The dedicated agent node consumes it (FOR UPDATE SKIP LOCKED) and stamps
  status/result. Indexed on (status, created_at) for the claim query and
  (account_id, status) for the reconciler's dedup check.
- marketing_accounts.schedule_enabled / override_action / override_until:
  toggle + manual override for the BRT schedule reconciler.

Downgrade drops the columns + table.
"""
from alembic import op

revision = "0151_marketing_commands_schedule"
down_revision = "0150_bling_order_ship_date"
branch_labels = None
depends_on = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.marketing_commands (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            account_id UUID NOT NULL
                REFERENCES {SCHEMA}.marketing_accounts(id) ON DELETE CASCADE,
            campaign_external_id VARCHAR(128),
            platform VARCHAR(32) NOT NULL,
            action VARCHAR(32) NOT NULL,
            payload JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            status VARCHAR(16) NOT NULL DEFAULT 'pending',
            result TEXT,
            attempts INTEGER NOT NULL DEFAULT 0,
            source VARCHAR(16) NOT NULL DEFAULT 'manual',
            created_by_user_id UUID
                REFERENCES {SCHEMA}.users(id) ON DELETE SET NULL,
            claimed_at TIMESTAMPTZ,
            completed_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        f"""
        CREATE INDEX IF NOT EXISTS ix_marketing_commands_status_created
        ON {SCHEMA}.marketing_commands (status, created_at)
        """
    )
    op.execute(
        f"""
        CREATE INDEX IF NOT EXISTS ix_marketing_commands_account_status
        ON {SCHEMA}.marketing_commands (account_id, status)
        """
    )
    op.execute(
        f"""
        ALTER TABLE {SCHEMA}.marketing_accounts
            ADD COLUMN IF NOT EXISTS schedule_enabled BOOLEAN NOT NULL DEFAULT false,
            ADD COLUMN IF NOT EXISTS override_action VARCHAR(16),
            ADD COLUMN IF NOT EXISTS override_until TIMESTAMPTZ
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        ALTER TABLE {SCHEMA}.marketing_accounts
            DROP COLUMN IF EXISTS override_until,
            DROP COLUMN IF EXISTS override_action,
            DROP COLUMN IF EXISTS schedule_enabled
        """
    )
    op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.marketing_commands")
