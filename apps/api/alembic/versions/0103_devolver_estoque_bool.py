"""devolver_estoque: TEXT -> BOOLEAN

Revision ID: 0103
Revises: 0102
Create Date: 2026-05-28
"""

from alembic import op
import sqlalchemy as sa

revision = "0103"
down_revision = "0102"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE devolutions
        ALTER COLUMN devolver_estoque
        TYPE BOOLEAN
        USING CASE
            WHEN devolver_estoque IS NULL THEN FALSE
            WHEN lower(trim(devolver_estoque)) IN ('true', '1', 'sim', 's') THEN TRUE
            ELSE FALSE
        END
        """
    )
    op.execute(
        "ALTER TABLE devolutions ALTER COLUMN devolver_estoque SET NOT NULL"
    )
    op.execute(
        "ALTER TABLE devolutions ALTER COLUMN devolver_estoque SET DEFAULT FALSE"
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE devolutions
        ALTER COLUMN devolver_estoque
        TYPE TEXT
        USING CASE WHEN devolver_estoque THEN 'true' ELSE NULL END
        """
    )
    op.execute(
        "ALTER TABLE devolutions ALTER COLUMN devolver_estoque DROP NOT NULL"
    )
    op.execute(
        "ALTER TABLE devolutions ALTER COLUMN devolver_estoque DROP DEFAULT"
    )
