"""marketing: per-integration department + bling_loja_id + ads_enabled

Revision ID: 0067_integration_marketing_fields
Revises: 0066_marketing_campaigns
Create Date: 2026-05-20

Each platform integration (Shopee/ML/Amazon) corresponds to one department
(celular | mala | eletro). The Marketing aggregator reads `department` to
build per-department views without needing campaign-level tagging. The
existing `Store.bling_store_id` already maps a Store to its Bling loja —
`Integration.bling_loja_id` is an explicit override for cases where the
operator wants to route revenue through a different loja than the Store's
default (rare). `ads_enabled` is the per-integration opt-in flag for the
marketing-shopee-pull cron so prod Bling integrations don't accidentally
get hit by Shopee Ads pulls.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0067_integration_marketing_fields"
down_revision: str | None = "0066_marketing_campaigns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column(
        "integrations",
        sa.Column("department", sa.String(32), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "integrations",
        sa.Column("bling_loja_id", sa.BigInteger(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "integrations",
        sa.Column(
            "ads_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("integrations", "ads_enabled", schema=SCHEMA)
    op.drop_column("integrations", "bling_loja_id", schema=SCHEMA)
    op.drop_column("integrations", "department", schema=SCHEMA)
