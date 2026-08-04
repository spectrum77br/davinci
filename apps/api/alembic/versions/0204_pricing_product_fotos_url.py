# ruff: noqa: E501
"""pricing_products.fotos_url: link das fotos do produto (pasta do MEGA)

Pra anunciar um aparelho o operador precisa das fotos de todas as cores e
hoje caça a pasta manualmente no MEGA. A coluna guarda o link por produto;
a aba Produtos da Tabela de Preços mostra um ícone de câmera que abre o
link pra visualizar/baixar as fotos.
"""

from alembic import op
import sqlalchemy as sa

revision: str = "0204_pricing_product_fotos_url"
down_revision: str | None = "0203_nf_etiqueta_arquivo_nf_pdf"
branch_labels = None
depends_on = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column(
        "pricing_products",
        sa.Column("fotos_url", sa.Text(), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("pricing_products", "fotos_url", schema=SCHEMA)
