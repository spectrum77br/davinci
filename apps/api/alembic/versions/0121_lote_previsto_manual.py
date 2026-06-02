"""import_lotes + previsto_manual (override do computed pra celular)

Revision ID: 0121_lote_previsto_manual
Revises: 0120_celular_frete
Create Date: 2026-06-02

Aba Importação do Celular precisa de previsto editável no header do
lote ativo. Hoje `previsto` é computed em _enrich_lote via
SUM(qty × custo_bling) dos items — funciona pra Mala mas pra Celular
o operador quer informar manualmente.

Schema: ADD COLUMN previsto_manual NUMERIC(12,2) nullable. Quando
setado, override o computed; quando NULL, usa o computed (= comportamento
atual da Mala, preservado).

Idempotente. Downgrade: DROP COLUMN.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0121_lote_previsto_manual"
down_revision = "0120_celular_frete"
branch_labels = None
depends_on = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute(
        f"ALTER TABLE {SCHEMA}.import_lotes "
        f"ADD COLUMN IF NOT EXISTS previsto_manual NUMERIC(12,2)"
    )


def downgrade() -> None:
    op.execute(
        f"ALTER TABLE {SCHEMA}.import_lotes DROP COLUMN IF EXISTS previsto_manual"
    )
