# ruff: noqa: E501
"""pricing_accounts.offer: campo livre "oferta" na aba Contas.

Mesmo padrão de discount/affiliate/ads/coupon (migration 0173): texto livre
rotulado, exibido como coluna própria na aba Contas e embaixo do nome da
loja na grade da Tabela de Preços.
"""

from alembic import op
import sqlalchemy as sa

revision: str = "0213_pricing_accounts_offer"
down_revision: str | None = "0212_bling_orders_ship_deadline"
branch_labels = None
depends_on = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column(
        "pricing_accounts",
        sa.Column("offer", sa.Text(), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("pricing_accounts", "offer", schema=SCHEMA)
