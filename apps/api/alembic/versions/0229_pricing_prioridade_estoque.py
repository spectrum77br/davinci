"""Coluna Prioridade (de estoque) na Tabela de Preços → Produtos.

Eduardo (2026-08-27): "vamos fazer uma coluna nova chamada prioridades,
antes de validar margem, sistema verifica na tabela prioridades estoque do
produto em outro tag, por exemplo dg53.ci, e dg053.sp, a venda saiu para
.ci [...] mas a prioridade para aquele produto esta .sp ja troca para esse
estoque" / "a tag que eu colocar la, o sku com a tag, ja deve trocar,
porque a prioridade e ele".

Guarda uma tag de estoque (ci/pi/ra/sa/sp/us/cd) por produto da tabela de
preços. O robô de troca (services/prioridade_estoque.py) usa isso pra
trocar o item do pedido no Bling ANTES da emissão de NF, desde que o SKU
alvo exista no Bling e tenha saldo virtual suficiente. NULL = sem
prioridade, nada muda pra esse produto.
"""

import sqlalchemy as sa

from alembic import op

revision: str = "0229_pricing_prioridade_estoque"
down_revision: str | None = "0228_store_faturador_por_tipo"
branch_labels = None
depends_on = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column(
        "pricing_products",
        sa.Column("prioridade_estoque", sa.String(8), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("pricing_products", "prioridade_estoque", schema=SCHEMA)
