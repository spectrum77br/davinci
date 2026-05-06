"""add 'backfill_ml_stock' to background_job_type enum (Fase 4b.ML)

Revision ID: 0006_backfill_ml_stock_enum
Revises: 0005_sync_logs
Create Date: 2026-05-06
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0006_backfill_ml_stock_enum"
down_revision: Union[str, None] = "0005_sync_logs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "davinci"


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE can't run inside a transaction block in older
    # Postgres; use COMMIT-friendly form. PG18 allows it inside a tx.
    op.execute(
        f"ALTER TYPE \"{SCHEMA}\".background_job_type "
        f"ADD VALUE IF NOT EXISTS 'backfill_ml_stock'"
    )


def downgrade() -> None:
    # Postgres has no DROP VALUE on enum; downgrade is a no-op. Recreating the
    # type would require rebuilding every column that references it.
    pass
