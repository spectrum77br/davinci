"""add 'tiktok' and 'temu' to integration_platform enum

Revision ID: 0016_integration_platform_tiktok_temu
Revises: 0015_cadastros_raw_links
Create Date: 2026-05-08
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0016_integration_platform_tiktok_temu"
down_revision: Union[str, None] = "0015_cadastros_raw_links"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute(
        f"ALTER TYPE \"{SCHEMA}\".integration_platform "
        f"ADD VALUE IF NOT EXISTS 'tiktok'"
    )
    op.execute(
        f"ALTER TYPE \"{SCHEMA}\".integration_platform "
        f"ADD VALUE IF NOT EXISTS 'temu'"
    )


def downgrade() -> None:
    # Postgres has no DROP VALUE on enum; downgrade is a no-op.
    pass
