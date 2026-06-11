"""import_lote_items: override de SKU destino do estoque no Bling

Revision ID: 0138_import_lote_item_target_sku
Revises: 0137_import_lote_item_bling_stock
Create Date: 2026-06-11

Permite o operador redirecionar a entrada de estoque do Bling pra um
SKU diferente do registrado no ImportProduct. Ex.: o lote tem item
i203.sa, mas no Bling existem i203.sa e i203.sp (mesmo iPad 11, tags
diferentes) — operador escolhe pra qual SKU mandar a quantidade ao
fechar o lote.

NULL = comportamento atual (vai pro SKU do ImportProduct).
Preenchido = service usa esse SKU pra resolver bling_product_id.

Só faz sentido na categoria 'celular' (única que dispara entrada de
estoque ao fechar lote). Não validamos no DB — frontend só expõe o
dropdown nessa categoria.
"""

from alembic import op

revision = "0138_import_lote_item_target_sku"
down_revision = "0137_import_lote_item_bling_stock"
branch_labels = None
depends_on = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute(
        f"ALTER TABLE {SCHEMA}.import_lote_items "
        f"ADD COLUMN IF NOT EXISTS bling_stock_target_sku VARCHAR(50)"
    )


def downgrade() -> None:
    op.execute(
        f"ALTER TABLE {SCHEMA}.import_lote_items "
        f"DROP COLUMN IF EXISTS bling_stock_target_sku"
    )
