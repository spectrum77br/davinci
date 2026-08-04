# ruff: noqa: E501
"""pricing_products.fotos_path: caminho da pasta de fotos DENTRO da conta MEGA

fotos_url (0204) é o link público de visualização. fotos_path é o caminho
remoto (ex.: "/Fotos Produtos/Redmi 13C") que a sincronização automática e
o upload direto usam pra saber ONDE listar/subir fotos via MEGAcmd.
"""

from alembic import op
import sqlalchemy as sa

revision: str = "0205_pricing_product_fotos_path"
down_revision: str | None = "0204_pricing_product_fotos_url"
branch_labels = None
depends_on = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column(
        "pricing_products",
        sa.Column("fotos_path", sa.Text(), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("pricing_products", "fotos_path", schema=SCHEMA)
