# ruff: noqa: E501
"""Cotação — 3 tabelas independentes para a aba Cotação da Importação.

A aba Cotação replica a planilha de comparação de preços entre
fabricantes. É INDEPENDENTE do resto do módulo (não puxa de
import_products, não tem fórmulas). Modelo:

  * cotacao_fabricantes — bloco de coluna (3 sub-cols: capacidade/R$/USD)
    com um cabeçalho de 4 observações livres.
  * cotacao_produtos    — linha da tabela (um produto comparado).
  * cotacao_valores     — célula no cruzamento produto × fabricante.

`ordem` em fabricantes/produtos preserva a ordem de exibição
(insert-order por default — operador pode reordenar via PATCH).

Org-wide (sem user_id/company_id), gated pela permissão `importacao`.

Revision ID: 0094_cotacao_tables
Revises: 0093_merge_bling_sync_devolucoes
Create Date: 2026-05-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision: str = "0094_cotacao_tables"
down_revision: str | None = "0093_merge_bling_sync_devolucoes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.create_table(
        "cotacao_fabricantes",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("nome", sa.String(100), nullable=False, server_default=""),
        sa.Column("obs1", sa.Text(), nullable=True),
        sa.Column("obs2", sa.Text(), nullable=True),
        sa.Column("obs3", sa.Text(), nullable=True),
        sa.Column("obs4", sa.Text(), nullable=True),
        sa.Column("ordem", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        schema=SCHEMA,
    )

    op.create_table(
        "cotacao_produtos",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("nome", sa.String(150), nullable=False, server_default=""),
        sa.Column("ordem", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        schema=SCHEMA,
    )

    op.create_table(
        "cotacao_valores",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "fabricante_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.cotacao_fabricantes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "produto_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.cotacao_produtos.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # capacidade is free-form text — operator writes "20cm", "8 peças",
        # "tamanho M", etc. Not parsed.
        sa.Column("capacidade", sa.String(50), nullable=True),
        sa.Column("valor_real", sa.Numeric(12, 2), nullable=True),
        sa.Column("valor_usd", sa.Numeric(12, 2), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("fabricante_id", "produto_id", name="uq_cotacao_valores_fab_prod"),
        schema=SCHEMA,
    )

    op.create_index(
        "ix_cotacao_valores_fabricante",
        "cotacao_valores",
        ["fabricante_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_cotacao_valores_produto",
        "cotacao_valores",
        ["produto_id"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("ix_cotacao_valores_produto", table_name="cotacao_valores", schema=SCHEMA)
    op.drop_index("ix_cotacao_valores_fabricante", table_name="cotacao_valores", schema=SCHEMA)
    op.drop_table("cotacao_valores", schema=SCHEMA)
    op.drop_table("cotacao_produtos", schema=SCHEMA)
    op.drop_table("cotacao_fabricantes", schema=SCHEMA)
