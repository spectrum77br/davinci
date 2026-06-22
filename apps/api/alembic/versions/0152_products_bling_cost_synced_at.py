"""products.bling_cost_synced_at — marca quando o custo do Bling foi conferido.

Necessário para o refresh on-ingest do custo: na criação do pedido, se o
`bling_cost_price` do SKU estiver velho (synced_at NULL ou além do limite), a
ingestão busca o `precoCusto` fresco no Bling antes de carimbar o pedido — sem
martelar a API quando o custo já está recente. O cron diário também passa a
carimbar essa coluna a cada produto conferido (mudado ou não).

Revision ID: 0152_products_bling_cost_synced_at
Revises: 0151_marketing_commands_schedule
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0152_products_bling_cost_synced_at"
down_revision = "0151_marketing_commands_schedule"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column("bling_cost_synced_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("products", "bling_cost_synced_at")
