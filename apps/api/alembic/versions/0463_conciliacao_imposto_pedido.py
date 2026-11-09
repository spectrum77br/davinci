"""conciliacao_imposto_pedido — ICMS DIFAL da Shopee no pedido

Vinicius (09/11/2026): "cada coisa tem que se encaixar nos pedidos". A linha de
DIFAL da carteira da Shopee traz o nº do pedido no texto ("… referente ao pedido
<order_sn>"); o recálculo liga a linha ao pedido e o valor entra no Sobrou do pedido
(coluna nova), sem mexer no recebido nem na situação — é imposto, não a Shopee
tomando a venda de volta. Pedido de antes do início vira "pedido anterior ao início".

* `conciliacao_pedidos.imposto`: o DIFAL cobrado à parte do pedido (positivo = saiu).
  NULL = nenhum. O recálculo preenche; não há dado a migrar.

Revision ID: 0463_conciliacao_imposto_pedido
Revises: 0462_garantia_contato
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0463_conciliacao_imposto_pedido"
down_revision: str | None = "0462_garantia_contato"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
SCHEMA = "davinci"


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.add_column(
        "conciliacao_pedidos",
        sa.Column("imposto", sa.Numeric(14, 2), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("conciliacao_pedidos", "imposto", schema=SCHEMA)
