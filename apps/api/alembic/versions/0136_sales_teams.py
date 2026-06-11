"""Equipes de Vendas — store_info.sales_team + users.sales_teams

Revision ID: 0136_sales_teams
Revises: 0135_audit_em_andamento_data
Create Date: 2026-06-10

Equipes de Vendas (Partes 1 e 2):
  * store_info.sales_team INTEGER NULL — uma loja pertence a UMA equipe.
  * users.sales_teams JSONB NULL — um usuário pertence a VÁRIAS equipes
    (espelha o padrão de stock_tags, mas guarda lista de inteiros).

Nenhuma tabela separada de "equipes"; a equipe é só o número. Vínculos
loja↔equipe e usuário↔equipes ficam nas próprias tabelas.

Idempotente. Downgrade reverte.
"""
from alembic import op

revision = "0136_sales_teams"
down_revision = "0135_audit_em_andamento_data"
branch_labels = None
depends_on = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute(
        f"ALTER TABLE {SCHEMA}.store_info "
        f"ADD COLUMN IF NOT EXISTS sales_team INTEGER"
    )
    op.execute(
        f"ALTER TABLE {SCHEMA}.users "
        f"ADD COLUMN IF NOT EXISTS sales_teams JSONB"
    )


def downgrade() -> None:
    op.execute(
        f"ALTER TABLE {SCHEMA}.users DROP COLUMN IF EXISTS sales_teams"
    )
    op.execute(
        f"ALTER TABLE {SCHEMA}.store_info DROP COLUMN IF EXISTS sales_team"
    )
