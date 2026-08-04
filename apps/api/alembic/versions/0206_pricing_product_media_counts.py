# ruff: noqa: E501
"""pricing_products.fotos_count/videos_count: quantas mídias tem na pasta MEGA

Contagem por extensão feita pelo sidecar (mega-find recursivo na pasta
fotos_path) e gravada aqui pelo sync/refresh/upload — a aba Produtos mostra
os números ao lado do link, sem consultar o MEGA a cada render.
"""

from alembic import op
import sqlalchemy as sa

revision: str = "0206_pricing_product_media_counts"
down_revision: str | None = "0205_pricing_product_fotos_path"
branch_labels = None
depends_on = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column(
        "pricing_products",
        sa.Column("fotos_count", sa.Integer(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "pricing_products",
        sa.Column("videos_count", sa.Integer(), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("pricing_products", "videos_count", schema=SCHEMA)
    op.drop_column("pricing_products", "fotos_count", schema=SCHEMA)
