"""denuncia_casos_extra — o que a gente acompanha do caso no DaVinci (compra e processo)

Vinicius, 01/10/2026 (aba Casos): "igual compras, colocar por onde compramos, qual loja, número
do pedido, previsão de entrega" e, no jurídico, "número do processo, última movimentação, status
da última movimentação, link de consulta do processo — vamos logar no Jusbrasil para acompanhar".
O robô do mini não tem nada disso; quem preenche é a pessoa na ficha do caso. Uma linha por caso.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0355_denuncia_casos_extra"
down_revision: str | None = "0354_denuncia_anexos"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.create_table(
        "denuncia_casos_extra",
        sa.Column("caso_id", sa.BigInteger(), primary_key=True),
        sa.Column("compra_loja", sa.Text()),
        sa.Column("compra_pedido", sa.Text()),
        sa.Column("compra_previsao", sa.Date()),
        sa.Column("processo_numero", sa.Text()),
        sa.Column("processo_link", sa.Text()),
        sa.Column("mov_data", sa.Date()),
        sa.Column("mov_texto", sa.Text()),
        sa.Column("mov_status", sa.Text()),
        sa.Column("atualizado_por", sa.Text()),
        sa.Column(
            "atualizado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("denuncia_casos_extra", schema=SCHEMA)
