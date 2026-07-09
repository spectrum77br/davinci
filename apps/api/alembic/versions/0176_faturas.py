# ruff: noqa: E501
"""create faturas table

Assinaturas/planos recorrentes que o admin acompanha (ex.: plano de 12 meses
do Higgsfield). Um cron avisa 1 dia antes do vencimento (alerta na tela). A
renovação é manual: o admin edita `data_vencimento` pro próximo ciclo.

Revision ID: 0176_faturas
Revises: 0175_marketing_adspower_executor
Create Date: 2026-07-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision: str = "0176_faturas"
down_revision: str | None = "0175_marketing_adspower_executor"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.create_table(
        "faturas",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("servico", sa.Text(), nullable=False),
        sa.Column("plano", sa.Text(), nullable=True),
        sa.Column("valor", sa.Numeric(12, 2), nullable=True),
        sa.Column("data_vencimento", sa.Date(), nullable=False),
        # SET NULL so a deleted admin doesn't break their old faturas.
        sa.Column(
            "created_by",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        schema=SCHEMA,
    )
    op.create_index("ix_faturas_data_vencimento", "faturas", ["data_vencimento"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_index("ix_faturas_data_vencimento", table_name="faturas", schema=SCHEMA)
    op.drop_table("faturas", schema=SCHEMA)
