# ruff: noqa: E501
"""nf_faturamento: status por etapa (faturamento/etiqueta/impressao) por pedido

Tabela lida pelo Painel de Faturamento (aba NF R37-R39). Uma linha por pedido
do Bling; a automacao das fases seguintes grava/atualiza os status aqui. Pedido
sem linha aparece 'pendente' no painel (derivado no LEFT JOIN).

Revision ID: 0197_nf_faturamento
Revises: 0196_store_info_nf_cadastros
Create Date: 2026-07-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision: str = "0197_nf_faturamento"
down_revision: str | None = "0196_store_info_nf_cadastros"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.create_table(
        "nf_faturamento",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True),
        sa.Column("pedido_bling", sa.Text(), nullable=False),
        sa.Column("status_faturamento", sa.Text(), nullable=True),
        sa.Column("erro_faturamento", sa.Text(), nullable=True),
        sa.Column("status_etiqueta", sa.Text(), nullable=True),
        sa.Column("erro_etiqueta", sa.Text(), nullable=True),
        sa.Column("status_impressao", sa.Text(), nullable=True),
        sa.Column("erro_impressao", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("pedido_bling", name="uq_nf_faturamento_pedido_bling"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("nf_faturamento", schema=SCHEMA)
