"""add duoke column to users

Revision ID: 0022_user_duoke
Revises: 0021_cell_status_na_sv
Create Date: 2026-05-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_user_duoke"
down_revision: str | None = "0021_cell_status_na_sv"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("duoke", sa.String(255), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("users", "duoke", schema=SCHEMA)
