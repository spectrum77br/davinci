# ruff: noqa: E501
"""import_products: finaliza estado de bling sync (v2).

Migration 0091 introduziu bling_sync_status + bling_sync_marked_at
como stub (operador clicava o botão "Enviar pro Bling" e marcava
intent — não criava nada de fato no Bling porque BlingClient não
tinha create_product na época).

Agora a integração é real: worker ARQ chama Bling, cria produto
simples (formato="S"), gera Product local linkado. Esta migration
adiciona os 4 campos finais pra rastrear o ciclo completo:

  * bling_product_id        — id retornado pelo Bling
  * bling_sync_error        — última mensagem de erro
  * bling_sync_attempted_at — última tentativa (success ou error)
  * bling_sync_done_at      — quando entrou em 'sent'

bling_sync_marked_at fica por backcompat (operacionalmente == attempted).

Revision ID: 0102_import_products_bling_sync_v2
Revises: 0101_import_kit_pricing_sync
Create Date: 2026-05-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0102_import_products_bling_sync_v2"
down_revision: str | None = "0101_import_kit_pricing_sync"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column(
        "import_products",
        sa.Column("bling_product_id", sa.BigInteger(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "import_products",
        sa.Column("bling_sync_error", sa.Text(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "import_products",
        sa.Column("bling_sync_attempted_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "import_products",
        sa.Column("bling_sync_done_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("import_products", "bling_sync_done_at", schema=SCHEMA)
    op.drop_column("import_products", "bling_sync_attempted_at", schema=SCHEMA)
    op.drop_column("import_products", "bling_sync_error", schema=SCHEMA)
    op.drop_column("import_products", "bling_product_id", schema=SCHEMA)
