"""add 'servidor' to cadastro_tipo enum

Revision ID: 0014_cadastro_tipo_servidor
Revises: 0013_audit
Create Date: 2026-05-07
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0014_cadastro_tipo_servidor"
down_revision: str | None = "0013_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    # PG12+ allows ALTER TYPE ADD VALUE inside a transaction.
    op.execute(f"ALTER TYPE \"{SCHEMA}\".cadastro_tipo ADD VALUE IF NOT EXISTS 'servidor'")


def downgrade() -> None:
    # Postgres has no DROP VALUE for enums; recreate type to remove.
    op.execute(
        f"""
        ALTER TYPE "{SCHEMA}".cadastro_tipo RENAME TO cadastro_tipo_old;
        CREATE TYPE "{SCHEMA}".cadastro_tipo AS ENUM ('fone','email','dominio');
        ALTER TABLE "{SCHEMA}".cadastros
            ALTER COLUMN tipo TYPE "{SCHEMA}".cadastro_tipo
            USING tipo::text::"{SCHEMA}".cadastro_tipo;
        DROP TYPE "{SCHEMA}".cadastro_tipo_old;
        """
    )
