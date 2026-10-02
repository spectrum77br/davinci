"""Tipos de loja para plataformas de cadastro sem tabela de preços."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0359_store_manual_departments"
down_revision: str | None = "0358_atendimento_avaliacoes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column(
        "store_info",
        sa.Column("manual_departments", postgresql.ARRAY(sa.Text()), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("store_info", "manual_departments", schema=SCHEMA)
