"""Anexo PDF de cada certificação de suprimentos."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0264_suprimentos_pdf"
down_revision: str | None = "0263_commercial_team"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column(
        "financeiro_suprimentos", sa.Column("pdf_nome", sa.Text(), nullable=True), schema=SCHEMA
    )
    op.add_column(
        "financeiro_suprimentos",
        sa.Column("pdf_arquivo", sa.LargeBinary(), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("financeiro_suprimentos", "pdf_arquivo", schema=SCHEMA)
    op.drop_column("financeiro_suprimentos", "pdf_nome", schema=SCHEMA)
