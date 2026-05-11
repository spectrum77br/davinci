"""add listing_type to product_links and listings

Revision ID: 0020_product_links_listing_type
Revises: 0019_refresh_bling_stock_enum
Create Date: 2026-05-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020_product_links_listing_type"
down_revision: str | None = "0019_refresh_bling_stock_enum"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column(
        "product_links",
        sa.Column("listing_type", sa.String(64), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "listings",
        sa.Column("listing_type", sa.String(64), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("listings", "listing_type", schema=SCHEMA)
    op.drop_column("product_links", "listing_type", schema=SCHEMA)
