"""marketing: cache timestamp for shopee balance (6h TTL)

Revision ID: 0069_marketing_account_credit_balance_at
Revises: 0068_integration_consecutive_errors
Create Date: 2026-05-20

Shopee's `get_total_balance` endpoint has its own ultra-tight rate
limit — empirically <~1 call per minute per partner_id across ALL shops.
A naive loop that calls it for 13 shops fails 100%. The fix is to cache
the balance on `marketing_accounts.credit_balance` (already exists) and
refresh it lazily — only one shop per cron tick, picking whichever has
the oldest `credit_balance_at`. With a 6h TTL and a 30min cron, 13 shops
finish their first full refresh in ~6.5h.

This migration adds the `credit_balance_at` column. Existing
`credit_balance` is reused (it was already Float-typed in the model).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0069_marketing_account_credit_balance_at"
down_revision: str | None = "0068_integration_consecutive_errors"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column(
        "marketing_accounts",
        sa.Column("credit_balance_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("marketing_accounts", "credit_balance_at", schema=SCHEMA)
