"""user_settings phase 7 — notifications + thresholds

Revision ID: 0009_user_settings_phase7
Revises: 0008_alerts
Create Date: 2026-05-06
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009_user_settings_phase7"
down_revision: Union[str, None] = "0008_alerts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.add_column(
        "user_settings",
        sa.Column("sync_interval_minutes", sa.Integer(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "user_settings",
        sa.Column("low_stock_threshold", sa.Integer(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "user_settings",
        sa.Column(
            "notify_email",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("TRUE"),
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "user_settings",
        sa.Column(
            "notify_telegram",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("FALSE"),
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "user_settings",
        sa.Column(
            "notify_daily_sync",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("TRUE"),
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "user_settings",
        sa.Column("telegram_chat_id", sa.Text(), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    for col in (
        "telegram_chat_id",
        "notify_daily_sync",
        "notify_telegram",
        "notify_email",
        "low_stock_threshold",
        "sync_interval_minutes",
    ):
        op.drop_column("user_settings", col, schema=SCHEMA)
