"""drop import_products.custo_realizado — é computed, não persistido

Revision ID: 0123_drop_custo_realizado
Revises: 0122_celular_lote_layout
Create Date: 2026-06-02

Revert parcial da 0122: a coluna `custo_realizado` foi adicionada
como editável manual no commit 3c8fa72. Operador clarificou que é
COMPUTED — média ponderada pelo qty dos lotes onde o produto aparece:

  custo_realizado = Σ(qty_lote × custoBRL_lote) / Σ(qty_lote)

GET /products passa a calcular em tempo real (categoria=celular).
Dropa a coluna pra evitar drift entre valor persistido e o computed
no futuro.

Migration 0122 (taxa/frete_pct/adicional em lotes, valor_usd em items)
permanece — esses estão corretos.

Idempotente: DROP COLUMN IF EXISTS. Downgrade re-adiciona o campo
(nullable, sem dados — operador entendia que era computed agora).
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0123_drop_custo_realizado"
down_revision = "0122_celular_lote_layout"
branch_labels = None
depends_on = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute(
        f"ALTER TABLE {SCHEMA}.import_products DROP COLUMN IF EXISTS custo_realizado"
    )


def downgrade() -> None:
    op.execute(
        f"ALTER TABLE {SCHEMA}.import_products "
        f"ADD COLUMN IF NOT EXISTS custo_realizado NUMERIC(10,2)"
    )
