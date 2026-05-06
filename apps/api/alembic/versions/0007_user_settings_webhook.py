"""user_settings (Fase 5 — daily_sync_scheduler) + sync_log_action 'webhook_unmatched'

Revision ID: 0007_user_settings_webhook
Revises: 0006_backfill_ml_stock_enum
Create Date: 2026-05-06
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_user_settings_webhook"
down_revision: Union[str, None] = "0006_backfill_ml_stock_enum"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')

    op.create_table(
        "user_settings",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "daily_sync_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("FALSE"),
        ),
        sa.Column("daily_sync_time", sa.Time(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        schema=SCHEMA,
    )
    op.execute(
        f'CREATE INDEX ix_user_settings_daily '
        f'ON "{SCHEMA}".user_settings (daily_sync_enabled, daily_sync_time) '
        f'WHERE daily_sync_enabled = TRUE'
    )

    op.execute(
        f"ALTER TYPE \"{SCHEMA}\".sync_log_action "
        f"ADD VALUE IF NOT EXISTS 'webhook_unmatched'"
    )


def downgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.execute(f'DROP INDEX IF EXISTS "{SCHEMA}".ix_user_settings_daily')
    op.drop_table("user_settings", schema=SCHEMA)
    # Postgres has no DROP VALUE on enum; leaving 'webhook_unmatched' in place.
