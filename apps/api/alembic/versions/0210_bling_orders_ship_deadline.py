# ruff: noqa: E501
"""bling_orders.marketplace_ship_deadline: prazo de despacho do marketplace.

"Despachar até" que cada marketplace promete ao comprador — capturado pelo
sweep de envio direto da API (Shopee `ship_by_date`, TikTok `rts_sla_time`,
Amazon `LatestShipDate`, ML `/shipments/{id}/sla.expected_date`) e mostrado
na aba Pedidos do Controle de Estoque como "horário de corte" do pedido.
NULL = ainda não capturado (pedido novo, já enviado antes da feature, ou
loja sem integração).
"""

from alembic import op
import sqlalchemy as sa

revision: str = "0210_bling_orders_ship_deadline"
down_revision: str | None = "0209_marketing_creative_files"
branch_labels = None
depends_on = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column(
        "bling_orders",
        sa.Column("marketplace_ship_deadline", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("bling_orders", "marketplace_ship_deadline", schema=SCHEMA)
