# ruff: noqa: E501
"""pricing_products.cost_kit5..cost_kit8: celular passa a ter até 8 kits.

Pedido do Eduardo (2026-08-04): na aba Produtos, celular vai até kit 8;
mala e eletro usam só kit 1 (as colunas extras somem no front, mas os
dados kit2..4 existentes ficam intactos no banco).
"""

from alembic import op
import sqlalchemy as sa

revision: str = "0207_pricing_product_kits5_8"
down_revision: str | None = "0206_pricing_product_media_counts"
branch_labels = None
depends_on = None

SCHEMA = "davinci"


def upgrade() -> None:
    for n in (5, 6, 7, 8):
        op.add_column(
            "pricing_products",
            sa.Column(f"cost_kit{n}", sa.Numeric(10, 2), nullable=True),
            schema=SCHEMA,
        )


def downgrade() -> None:
    for n in (8, 7, 6, 5):
        op.drop_column("pricing_products", f"cost_kit{n}", schema=SCHEMA)
