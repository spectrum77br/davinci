"""alerts table (Fase 6)

Revision ID: 0008_alerts
Revises: 0007_user_settings_webhook
Create Date: 2026-05-06
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_alerts"
down_revision: Union[str, None] = "0007_user_settings_webhook"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "davinci"

ALERT_TYPES = (
    "low_stock",
    "sync_failure",
    "listing_banned",
    "requires_review",
    "daily_sync_completed",
    "token_expiring",
    "generic",
)
ALERT_SEVERITIES = ("info", "warning", "error", "success")


def upgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')

    bind = op.get_bind()
    sa.Enum(*ALERT_TYPES, name="alert_type", schema=SCHEMA).create(bind, checkfirst=True)
    sa.Enum(
        *ALERT_SEVERITIES, name="alert_severity", schema=SCHEMA
    ).create(bind, checkfirst=True)

    op.create_table(
        "alerts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "type",
            postgresql.ENUM(
                *ALERT_TYPES, name="alert_type", schema=SCHEMA, create_type=False
            ),
            nullable=False,
        ),
        sa.Column(
            "severity",
            postgresql.ENUM(
                *ALERT_SEVERITIES,
                name="alert_severity",
                schema=SCHEMA,
                create_type=False,
            ),
            nullable=False,
            server_default=sa.text("'info'"),
        ),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("dedupe_key", sa.Text(), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema=SCHEMA,
    )

    op.execute(
        f'CREATE INDEX ix_alerts_user_unread_created '
        f'ON "{SCHEMA}".alerts (user_id, read_at NULLS FIRST, created_at DESC)'
    )
    op.execute(
        f'CREATE UNIQUE INDEX ix_alerts_user_dedupe '
        f'ON "{SCHEMA}".alerts (user_id, dedupe_key) '
        f'WHERE dedupe_key IS NOT NULL'
    )


def downgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.execute(f'DROP INDEX IF EXISTS "{SCHEMA}".ix_alerts_user_dedupe')
    op.execute(f'DROP INDEX IF EXISTS "{SCHEMA}".ix_alerts_user_unread_created')
    op.drop_table("alerts", schema=SCHEMA)
    bind = op.get_bind()
    sa.Enum(name="alert_severity", schema=SCHEMA).drop(bind, checkfirst=True)
    sa.Enum(name="alert_type", schema=SCHEMA).drop(bind, checkfirst=True)
