# ruff: noqa: E501
"""nf_etiqueta_arquivo: etiqueta transformada por pedido (landing zone p/ impressao)

Guarda a etiqueta ja tratada pela regra de visualizacao (remetente=destinatario,
sem numero/cod. barras/chave da NF, sem nome do marketplace) como blob (bytea),
chaveada por pedido_bling. Servida por endpoint autenticado por cookie (mesmo
desenho de logistica_status_anexo) e impressa em Controle de Estoque -> Pedidos.

Revision ID: 0201_nf_etiqueta_arquivo
Revises: 0200_nf_command
Create Date: 2026-07-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision: str = "0201_nf_etiqueta_arquivo"
down_revision: str | None = "0200_nf_command"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.create_table(
        "nf_etiqueta_arquivo",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True),
        sa.Column("pedido_bling", sa.Text(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("blob", sa.LargeBinary(), nullable=False),
        sa.Column("created_by", PG_UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by"], [f"{SCHEMA}.users.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint("pedido_bling", name="uq_nf_etiqueta_arquivo_pedido_bling"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("nf_etiqueta_arquivo", schema=SCHEMA)
