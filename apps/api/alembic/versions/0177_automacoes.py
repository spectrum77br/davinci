# ruff: noqa: E501
"""create automacoes table

Catálogo manual das automações/crons do sistema que o admin mantém à mão pra
ter visibilidade do que está rodando (aba Automações na tela de Integrações).
NÃO controla nem executa nada — é só um registro editável.

Revision ID: 0177_automacoes
Revises: 0176_faturas
Create Date: 2026-07-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision: str = "0177_automacoes"
down_revision: str | None = "0176_faturas"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.create_table(
        "automacoes",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("nome", sa.Text(), nullable=False),
        sa.Column("descricao", sa.Text(), nullable=True),
        sa.Column("frequencia", sa.Text(), nullable=True),
        sa.Column("categoria", sa.Text(), nullable=True),
        sa.Column("ativa", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        # SET NULL so a deleted admin doesn't break their old entries.
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


def downgrade() -> None:
    op.drop_table("automacoes", schema=SCHEMA)
