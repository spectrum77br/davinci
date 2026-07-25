# ruff: noqa: E501
"""create nf_faturador table

Cadastro do FATURADOR (emissor da NF) do sistema de notas fiscais automáticas.
Cada linha = 1 tipo de faturador (bling avulso/celular, exclusivo, upseller
2/1/70/100%); lista extensível (admin inclui tipo novo manualmente, regra
programada depois).

Revision ID: 0194_nf_faturador
Revises: 0193_vw_bling_pedidos_store_info_fallback
Create Date: 2026-07-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision: str = "0194_nf_faturador"
down_revision: str | None = "0193_vw_bling_pedidos_store_info_fallback"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.create_table(
        "nf_faturador",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("nome", sa.Text(), nullable=False),
        sa.Column("modo", sa.Text(), nullable=False),
        sa.Column("nf_cheia", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("percentual", sa.Numeric(6, 3), nullable=True),
        sa.Column("sku_fonte", sa.Text(), nullable=True),
        sa.Column("nome_fonte", sa.Text(), nullable=True),
        sa.Column("ncm", sa.Text(), nullable=True),
        sa.Column("ads_power", sa.Text(), nullable=True),
        sa.Column("usuario", sa.Text(), nullable=True),
        sa.Column("senha_enc", sa.Text(), nullable=True),
        sa.Column("observacao", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
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
    op.drop_table("nf_faturador", schema=SCHEMA)
