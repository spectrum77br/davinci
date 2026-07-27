# ruff: noqa: E501
"""nf_command: outbox da importacao da planilha avulsa (Fase 3a-4)

Um comando = UM faturador com o subconjunto de pedidos daquela loja + o CSV
congelado (bytea). O executor local (AdsPower) faz poll de /agent/lease, importa
a planilha no Bling destino e reporta em /agent/commands/{id}/result. O login do
faturador e entregue no lease, nunca persistido no comando.

Revision ID: 0200_nf_command
Revises: 0199_nf_catalogo_mala_drop_sku_base
Create Date: 2026-07-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision: str = "0200_nf_command"
down_revision: str | None = "0199_nf_catalogo_mala_drop_sku_base"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.create_table(
        "nf_command",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True),
        sa.Column("faturador_id", PG_UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.Text(), server_default=sa.text("'import_avulsa'"), nullable=False),
        sa.Column("numeros", JSONB(), nullable=False),
        sa.Column("planilha", sa.LargeBinary(), nullable=False),
        sa.Column("nome_arquivo", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("source", sa.Text(), server_default=sa.text("'manual'"), nullable=False),
        sa.Column("created_by", PG_UUID(as_uuid=True), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["faturador_id"], [f"{SCHEMA}.nf_faturador.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], [f"{SCHEMA}.users.id"], ondelete="SET NULL"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_nf_command_status", "nf_command", ["status"], schema=SCHEMA
    )


def downgrade() -> None:
    op.drop_index("ix_nf_command_status", table_name="nf_command", schema=SCHEMA)
    op.drop_table("nf_command", schema=SCHEMA)
