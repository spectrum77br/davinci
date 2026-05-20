"""marketing module: accounts + schedules + metrics + decisions + patterns

Revision ID: 0065_marketing_module
Revises: 0065_refunds
Create Date: 2026-05-20

Chains after 0065_refunds (a recovery stub for an orphan prod stamp).
Numbering is preserved (both at 0065_*) because the revision_id is what
alembic uses, not the filename — but the file is loaded after refunds
to keep the natural reading order.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0065_marketing_module"
down_revision: str | None = "0065_refunds"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.create_table(
        "marketing_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("integration_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column("department", sa.String(32), nullable=False),
        sa.Column("acos_target", sa.Float(), nullable=False, server_default="8.0"),
        sa.Column("daily_budget", sa.Float(), nullable=True),
        sa.Column("agent_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("current_intensity", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("credit_balance", sa.Float(), nullable=True),
        sa.Column("spend_today", sa.Float(), nullable=False, server_default="0"),
        sa.Column("revenue_today", sa.Float(), nullable=False, server_default="0"),
        sa.Column("impressions_today", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["integration_id"], [f"{SCHEMA}.integrations.id"], name="fk_marketing_accounts_integration_id_integrations", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_marketing_accounts"),
        sa.UniqueConstraint("name", "platform", "department", name="uq_marketing_accounts_name_platform_department"),
        schema=SCHEMA,
    )
    op.create_index("ix_marketing_accounts_platform_department", "marketing_accounts", ["platform", "department"], schema=SCHEMA)

    op.create_table(
        "marketing_schedules",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("day_of_week", sa.Integer(), nullable=False),
        sa.Column("start_hour", sa.Integer(), nullable=False),
        sa.Column("end_hour", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], [f"{SCHEMA}.marketing_accounts.id"], name="fk_marketing_schedules_account_id_marketing_accounts", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_marketing_schedules"),
        schema=SCHEMA,
    )
    op.create_index("ix_marketing_schedules_account_id", "marketing_schedules", ["account_id"], schema=SCHEMA)

    op.create_table(
        "marketing_metrics",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("spend", sa.Float(), nullable=False, server_default="0"),
        sa.Column("revenue", sa.Float(), nullable=False, server_default="0"),
        sa.Column("impressions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("clicks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("orders", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("acos", sa.Float(), nullable=True),
        sa.Column("intensity", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], [f"{SCHEMA}.marketing_accounts.id"], name="fk_marketing_metrics_account_id_marketing_accounts", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_marketing_metrics"),
        schema=SCHEMA,
    )
    op.create_index("ix_marketing_metrics_account_id_timestamp", "marketing_metrics", ["account_id", "timestamp"], schema=SCHEMA)

    op.create_table(
        "marketing_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("reasoning", sa.Text(), nullable=False),
        sa.Column("params", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("market_intensity", sa.Integer(), nullable=False),
        sa.Column("in_base_window", sa.Boolean(), nullable=False),
        sa.Column("acos_at_time", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], [f"{SCHEMA}.marketing_accounts.id"], name="fk_marketing_decisions_account_id_marketing_accounts", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_marketing_decisions"),
        schema=SCHEMA,
    )
    op.create_index("ix_marketing_decisions_account_id_timestamp", "marketing_decisions", ["account_id", "timestamp"], schema=SCHEMA)

    op.create_table(
        "marketing_patterns",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pattern_type", sa.String(64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("data", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], [f"{SCHEMA}.marketing_accounts.id"], name="fk_marketing_patterns_account_id_marketing_accounts", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_marketing_patterns"),
        schema=SCHEMA,
    )
    op.create_index("ix_marketing_patterns_account_id", "marketing_patterns", ["account_id"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_index("ix_marketing_patterns_account_id", table_name="marketing_patterns", schema=SCHEMA)
    op.drop_table("marketing_patterns", schema=SCHEMA)
    op.drop_index("ix_marketing_decisions_account_id_timestamp", table_name="marketing_decisions", schema=SCHEMA)
    op.drop_table("marketing_decisions", schema=SCHEMA)
    op.drop_index("ix_marketing_metrics_account_id_timestamp", table_name="marketing_metrics", schema=SCHEMA)
    op.drop_table("marketing_metrics", schema=SCHEMA)
    op.drop_index("ix_marketing_schedules_account_id", table_name="marketing_schedules", schema=SCHEMA)
    op.drop_table("marketing_schedules", schema=SCHEMA)
    op.drop_index("ix_marketing_accounts_platform_department", table_name="marketing_accounts", schema=SCHEMA)
    op.drop_table("marketing_accounts", schema=SCHEMA)
