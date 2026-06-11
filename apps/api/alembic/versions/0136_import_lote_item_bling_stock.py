"""import_lote_items: campos pra entrada de estoque no Bling ao fechar lote

Revision ID: 0136_import_lote_item_bling_stock
Revises: 0135_audit_em_andamento_data
Create Date: 2026-06-11

Ao fechar um lote da categoria 'celular' (PATCH /lotes/{id} setando
fechamento), o backend enfileira um job arq que lança a quantidade de
cada item como entrada de estoque no Bling (POST /Api/v3/estoques
operação 'E'). Estes 3 campos servem pra rastrear o estado por item:

- bling_stock_status: NULL | 'pending' | 'sent' | 'error' | 'skipped'
- bling_stock_error:  mensagem da última falha (best-effort)
- bling_stock_pushed_at: timestamp da entrada bem-sucedida. Quando
  preenchido, o job pula a row — idempotente em re-fechamentos /
  re-execuções do job.

Idempotente. Downgrade reverso.
"""

from alembic import op

revision = "0136_import_lote_item_bling_stock"
down_revision = "0135_audit_em_andamento_data"
branch_labels = None
depends_on = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute(
        f"ALTER TABLE {SCHEMA}.import_lote_items "
        f"ADD COLUMN IF NOT EXISTS bling_stock_status VARCHAR(20)"
    )
    op.execute(
        f"ALTER TABLE {SCHEMA}.import_lote_items "
        f"ADD COLUMN IF NOT EXISTS bling_stock_error TEXT"
    )
    op.execute(
        f"ALTER TABLE {SCHEMA}.import_lote_items "
        f"ADD COLUMN IF NOT EXISTS bling_stock_pushed_at TIMESTAMPTZ"
    )


def downgrade() -> None:
    op.execute(
        f"ALTER TABLE {SCHEMA}.import_lote_items "
        f"DROP COLUMN IF EXISTS bling_stock_pushed_at"
    )
    op.execute(
        f"ALTER TABLE {SCHEMA}.import_lote_items "
        f"DROP COLUMN IF EXISTS bling_stock_error"
    )
    op.execute(
        f"ALTER TABLE {SCHEMA}.import_lote_items "
        f"DROP COLUMN IF EXISTS bling_stock_status"
    )
