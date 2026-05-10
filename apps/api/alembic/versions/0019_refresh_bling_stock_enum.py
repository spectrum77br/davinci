"""add 'refresh_bling_stock' to background_job_type enum

Revision ID: 0019_refresh_bling_stock_enum
Revises: 0018_store_info_integration
Create Date: 2026-05-10
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0019_refresh_bling_stock_enum"
down_revision: str | None = "0018_store_info_integration"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute(
        f"ALTER TYPE \"{SCHEMA}\".background_job_type "
        f"ADD VALUE IF NOT EXISTS 'refresh_bling_stock'"
    )


def downgrade() -> None:
    pass
