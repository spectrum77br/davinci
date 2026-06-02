"""celular: taxa/frete_pct/adicional por lote + valor_usd por lote_item + custo_realizado por produto

Revision ID: 0122_celular_lote_layout
Revises: 0121_lote_previsto_manual
Create Date: 2026-06-02

Aba Importação Celular passa a usar layout igual ao Mala (header de
lotes ativos no topo + colunas dinâmicas por lote no body) com extras
próprios:

  1. ImportLote + (taxa, frete_pct, adicional)
       Mala não usa — ficam NULL. Celular: prefilled da
       ImportCotacaoParams na criação do lote, depois operador pode
       editar por lote (cada remessa pode ter taxa/frete diferentes).
  2. ImportLoteItem + valor_usd
       Pra celular, o `valor_usd` é POR LOTE (mesmo produto em lotes
       diferentes pode ter valor distinto). `ImportProduct.valor_usd`
       (etapa 3) continua existindo — usado pela aba Cotação como
       referência. Body do `custo` BRL = valor_usd × taxa × (1+frete)
       + adicional, com params do LOTE.
  3. ImportProduct + custo_realizado
       Coluna J do Excel ("media do custo") — operador preenche
       manualmente, placeholder "media do custo".

Idempotente. Downgrade reverso.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0122_celular_lote_layout"
down_revision = "0121_lote_previsto_manual"
branch_labels = None
depends_on = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute(
        f"ALTER TABLE {SCHEMA}.import_lotes "
        f"ADD COLUMN IF NOT EXISTS taxa NUMERIC(8,4)"
    )
    op.execute(
        f"ALTER TABLE {SCHEMA}.import_lotes "
        f"ADD COLUMN IF NOT EXISTS frete_pct NUMERIC(6,4)"
    )
    op.execute(
        f"ALTER TABLE {SCHEMA}.import_lotes "
        f"ADD COLUMN IF NOT EXISTS adicional NUMERIC(10,2)"
    )
    op.execute(
        f"ALTER TABLE {SCHEMA}.import_lote_items "
        f"ADD COLUMN IF NOT EXISTS valor_usd NUMERIC(10,2)"
    )
    op.execute(
        f"ALTER TABLE {SCHEMA}.import_products "
        f"ADD COLUMN IF NOT EXISTS custo_realizado NUMERIC(10,2)"
    )


def downgrade() -> None:
    op.execute(
        f"ALTER TABLE {SCHEMA}.import_products DROP COLUMN IF EXISTS custo_realizado"
    )
    op.execute(
        f"ALTER TABLE {SCHEMA}.import_lote_items DROP COLUMN IF EXISTS valor_usd"
    )
    op.execute(f"ALTER TABLE {SCHEMA}.import_lotes DROP COLUMN IF EXISTS adicional")
    op.execute(f"ALTER TABLE {SCHEMA}.import_lotes DROP COLUMN IF EXISTS frete_pct")
    op.execute(f"ALTER TABLE {SCHEMA}.import_lotes DROP COLUMN IF EXISTS taxa")
