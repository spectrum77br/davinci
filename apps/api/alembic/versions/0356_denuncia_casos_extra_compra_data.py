"""denuncia_casos_extra.compra_data — a data da compra de prova na tabela de Casos

Vinicius, 01/10/2026: "coloca as compras assim [como no painel Devoluções] com data, loja e
pedido". A data vem do robô quando ele registrou a compra; quando não registrou, a pessoa digita.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0356_denuncia_casos_extra_compra_data"
down_revision: str | None = "0355_denuncia_casos_extra"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column("denuncia_casos_extra", sa.Column("compra_data", sa.Date()), schema=SCHEMA)


def downgrade() -> None:
    op.drop_column("denuncia_casos_extra", "compra_data", schema=SCHEMA)
