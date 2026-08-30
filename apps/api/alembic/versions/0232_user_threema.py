"""add threema column to users

Revision ID: 0232_user_threema
Revises: 0231_nf_nota
Create Date: 2026-08-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0232_user_threema"
down_revision: str | None = "0231_nf_nota"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("threema", sa.String(255), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("users", "threema", schema=SCHEMA)
