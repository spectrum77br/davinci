"""celular: custo_manual em import_lote_items pra lotes sem taxa/frete

Revision ID: 0128_lote_item_custo_manual
Revises: 0127_bling_kit_components
Create Date: 2026-06-03

Lotes como I48 (acessórios em massa) chegam sem transportadora regular
— `lote.taxa IS NULL` ou `lote.frete_pct IS NULL`. Nesses, a fórmula
`valor_usd × taxa × (1+frete) + adicional` não se aplica e o operador
quer digitar o custo BRL direto por linha. `custo_manual` guarda esse
valor; o realizado do lote usa ele em vez da fórmula quando o lote
não tem taxa/frete.

Idempotente. Downgrade reverso.
"""

from alembic import op

revision = "0128_lote_item_custo_manual"
down_revision = "0127_bling_kit_components"
branch_labels = None
depends_on = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute(
        f"ALTER TABLE {SCHEMA}.import_lote_items "
        f"ADD COLUMN IF NOT EXISTS custo_manual NUMERIC(10,2)"
    )


def downgrade() -> None:
    op.execute(
        f"ALTER TABLE {SCHEMA}.import_lote_items DROP COLUMN IF EXISTS custo_manual"
    )
