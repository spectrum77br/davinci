"""add 'export_notas_fiscais' to background_job_type enum

Job assíncrono de export de NF-e (xlsx/zip) roda no worker pra fugir do
timeout do proxy nos lotes grandes (>500 notas) — ver
`app.services.notas_fiscais_export`.

Revision ID: 0141_export_notas_enum
Revises: 0140_bling_notas
Create Date: 2026-06-13
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0141_export_notas_enum"
down_revision: str | None = "0140_bling_notas"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    # PG12+ allows ALTER TYPE ADD VALUE inside a transaction.
    op.execute(
        f"ALTER TYPE \"{SCHEMA}\".background_job_type "
        f"ADD VALUE IF NOT EXISTS 'export_notas_fiscais'"
    )


def downgrade() -> None:
    # Postgres has no DROP VALUE for enums; deixamos o valor órfão.
    pass
