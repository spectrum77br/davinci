"""marketing: integrations.last_ads_sync_at for Shopee round-robin

Revision ID: 0071_integration_last_ads_sync_at
Revises: 0070_merge_marketing_refunds_heads
Create Date: 2026-05-20

Shopee's per-partner_id Ads throttle is so tight (<~1 call/min) that the
"batch every 30min" pattern can't fit a 13-shop loop. Instead we sync ONE
shop per cron tick, choosing whichever has the oldest `last_ads_sync_at`
(NULL → never synced → first in line). With cron every 5min × 13 shops,
each shop refreshes in ~65min. This column is the round-robin cursor.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0071_integration_last_ads_sync_at"
down_revision: str | None = "0070_merge_marketing_refunds_heads"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column(
        "integrations",
        sa.Column("last_ads_sync_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("integrations", "last_ads_sync_at", schema=SCHEMA)
