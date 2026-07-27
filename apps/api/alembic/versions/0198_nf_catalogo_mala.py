# ruff: noqa: E501
"""nf_catalogo_mala: valor cheio da NF de mala por (modelo, tamanho)

Catalogo da aba `catalogo mala` do xlsx. A NF cheia de mala usa um valor fixo
por (modelo, tamanho) casado com NCM 4202.12.10, NAO o valor de venda. O vinculo
`sku_base` (codigo-base do SKU da mala, ex. b001) e editavel e comeca NULL — o
admin preenche na tela; enquanto NULL o motor cai no valor de venda.

Revision ID: 0198_nf_catalogo_mala
Revises: 0197_nf_faturamento
Create Date: 2026-07-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision: str = "0198_nf_catalogo_mala"
down_revision: str | None = "0197_nf_faturamento"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.create_table(
        "nf_catalogo_mala",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True),
        sa.Column("modelo", sa.Text(), nullable=False),
        sa.Column("tamanho", sa.Text(), nullable=True),
        sa.Column("valor", sa.Numeric(12, 2), nullable=False),
        sa.Column("sku_base", sa.Text(), nullable=True),
        sa.Column("ncm", sa.Text(), server_default=sa.text("'4202.12.10'"), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "created_by",
            PG_UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_nf_catalogo_mala_sku_base",
        "nf_catalogo_mala",
        ["sku_base"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("ix_nf_catalogo_mala_sku_base", table_name="nf_catalogo_mala", schema=SCHEMA)
    op.drop_table("nf_catalogo_mala", schema=SCHEMA)
