"""add 'magalu' to integration_platform enum

`marketplace` e `pricing_platform` já carregam 'magalu' desde 0002/0011; só
faltava o enum `integration_platform` pra permitir criar integrações Magalu
(conector OAuth nativo, no molde do Mercado Livre).

Revision ID: 0172_integration_platform_magalu
Revises: 0171_integration_vacation_mode
Create Date: 2026-07-06
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0172_integration_platform_magalu"
down_revision: str | None = "0171_integration_vacation_mode"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    # PG12+ allows ALTER TYPE ADD VALUE inside a transaction.
    op.execute(f"ALTER TYPE \"{SCHEMA}\".integration_platform ADD VALUE IF NOT EXISTS 'magalu'")


def downgrade() -> None:
    # Postgres has no DROP VALUE for enums; would need to recreate the type
    # and re-cast every column using it. Not worth it for an additive change.
    pass
