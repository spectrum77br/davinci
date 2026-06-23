"""valuation_marketplace_saldo_diario — snapshot diário do saldo por loja/marketplace.

A rotina externa AdsPower Contabilidade (run_all.py) raspa Mercado Pago/
Shopee/Amazon por perfil e hoje só persiste o TOTAL GERAL em
valuation.receber (o detalhamento por loja só vai no Threema). Esta tabela
guarda o breakdown por loja × marketplace para a aba "Saldo Marketplace"
da página /financeiro/valuation ler.

Produtor continua sendo a rotina LOCAL (AdsPower é desktop-only, sem API de
saldo — não dá pra rodar no worker remoto). Ela ganha um UPSERT aqui além do
Threema + valuation.receber. `total_a_receber` deve coincidir com
valuation.receber do mesmo dia.

Revision ID: 0155_valuation_marketplace_saldo_diario
Revises: 0154_vw_bling_pedidos_marketplace_item_weight
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0155_valuation_marketplace_saldo_diario"
down_revision = "0154_vw_bling_pedidos_marketplace_item_weight"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "valuation_marketplace_saldo_diario",
        sa.Column("data", sa.Date(), primary_key=True),
        sa.Column(
            "total_a_receber",
            sa.Numeric(16, 2),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "total_disponivel",
            sa.Numeric(16, 2),
            nullable=False,
            server_default=sa.text("0"),
        ),
        # por_loja: lista de
        #   { "loja": str,
        #     "ml":     {"disponivel": num|null, "a_receber": num|null},
        #     "shopee": {...}, "amazon": {...}, ... }
        # Chaves de marketplace dinâmicas (suporta marketplaces futuros).
        sa.Column(
            "por_loja",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
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
    op.drop_table("valuation_marketplace_saldo_diario")
