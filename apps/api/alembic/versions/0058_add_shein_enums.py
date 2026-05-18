"""add 'shein' to pricing_platform, department, and integration_platform enums

Revision ID: 0058_add_shein_enums
Revises: 0057_recreate_mv_conciliacao_margens
Create Date: 2026-05-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0058_add_shein_enums"
down_revision: str | None = "0057_recreate_mv_conciliacao_margens"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    # PG12+ allows ALTER TYPE ADD VALUE inside a transaction.
    op.execute(f"ALTER TYPE \"{SCHEMA}\".pricing_platform ADD VALUE IF NOT EXISTS 'shein'")
    op.execute(f"ALTER TYPE \"{SCHEMA}\".department ADD VALUE IF NOT EXISTS 'shein'")
    op.execute(f"ALTER TYPE \"{SCHEMA}\".integration_platform ADD VALUE IF NOT EXISTS 'shein'")


def downgrade() -> None:
    # Postgres has no DROP VALUE for enums; would need to recreate each type
    # and re-cast every column using them. Not worth it for an additive change.
    pass
