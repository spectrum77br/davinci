"""add 'NA' and 'SV' to cell_status enum

Revision ID: 0021_cell_status_na_sv
Revises: 0020_product_links_listing_type
Create Date: 2026-05-12
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0021_cell_status_na_sv"
down_revision: str | None = "0020_product_links_listing_type"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute(
        f"ALTER TYPE \"{SCHEMA}\".cell_status ADD VALUE IF NOT EXISTS 'NA'"
    )
    op.execute(
        f"ALTER TYPE \"{SCHEMA}\".cell_status ADD VALUE IF NOT EXISTS 'SV'"
    )


def downgrade() -> None:
    pass
