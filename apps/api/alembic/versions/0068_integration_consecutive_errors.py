"""marketing: consecutive_errors counter on integrations (for 3x-failure alerts)

Revision ID: 0068_integration_consecutive_errors
Revises: 0067_integration_marketing_fields
Create Date: 2026-05-20

The marketing sync orchestrators increment this on every failure and
reset to 0 on success. The alerts module fires Telegram at exactly the
3rd consecutive failure so the operator sees the issue without getting
spammed on a flaky API. Stored on Integration (not MarketingAccount)
because the failure is at the platform-API layer.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0068_integration_consecutive_errors"
down_revision: str | None = "0067_integration_marketing_fields"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column(
        "integrations",
        sa.Column(
            "consecutive_errors",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("integrations", "consecutive_errors", schema=SCHEMA)
