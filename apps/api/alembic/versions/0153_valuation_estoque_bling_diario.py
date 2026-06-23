"""valuation_estoque_bling_diario — snapshot diário do estoque por local.

Substitui a rotina externa estoque-bling-diario (que só persistia o TOTAL
em valuation.estoque e mandava o breakdown no Threema). Esta tabela guarda
o breakdown por local (PI, SA, SP, RA, CD, CI, US, Eletro, Mala, Outros)
para a aba "Estoque Bling" da página /financeiro/valuation ler.

A linha existe uma vez por dia (PK data). O cron `valuation_estoque_snapshot`
no worker arq grava o snapshot e também atualiza valuation.estoque (total)
para manter a aba Resumo coerente — a coluna `total_valor` aqui e
`valuation.estoque` do mesmo dia devem coincidir.

Revision ID: 0153_valuation_estoque_bling_diario
Revises: 0152_products_bling_cost_synced_at
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0153_valuation_estoque_bling_diario"
down_revision = "0152_products_bling_cost_synced_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "valuation_estoque_bling_diario",
        sa.Column("data", sa.Date(), primary_key=True),
        sa.Column("total_qtd", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "total_valor",
            sa.Numeric(16, 2),
            nullable=False,
            server_default=sa.text("0"),
        ),
        # por_local: { "PI": {"qtd": int, "valor": numeric}, ... }
        sa.Column(
            "por_local",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("valuation_estoque_bling_diario")
