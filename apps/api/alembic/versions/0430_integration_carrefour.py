"""Enable Carrefour API connections without creating or activating accounts.

Revision ID: 0430_integration_carrefour
Revises: 0429_compra_contabilidade
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0430_integration_carrefour"
down_revision: str | None = "0429_compra_contabilidade"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute(f'ALTER TYPE "{SCHEMA}".integration_platform ADD VALUE IF NOT EXISTS \'carrefour\'')


def downgrade() -> None:
    # PostgreSQL cannot remove one enum value without rebuilding the type.
    # Preserve existing account and listing data when rolling back the code.
    pass
