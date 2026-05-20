"""marketing module: campaigns table (spreadsheet grid)

Revision ID: 0066_marketing_campaigns
Revises: 0065_marketing_module
Create Date: 2026-05-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0066_marketing_campaigns"
down_revision: str | None = "0065_marketing_module"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.create_table(
        "marketing_campaigns",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("external_id", sa.String(128), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("credit", sa.Float(), nullable=True),
        sa.Column("spend", sa.Float(), nullable=False, server_default="0"),
        sa.Column("revenue", sa.Float(), nullable=False, server_default="0"),
        sa.Column("impressions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("acos", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"],
            [f"{SCHEMA}.marketing_accounts.id"],
            name="fk_marketing_campaigns_account_id_marketing_accounts",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_marketing_campaigns"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_marketing_campaigns_account_id",
        "marketing_campaigns",
        ["account_id"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("ix_marketing_campaigns_account_id", table_name="marketing_campaigns", schema=SCHEMA)
    op.drop_table("marketing_campaigns", schema=SCHEMA)
