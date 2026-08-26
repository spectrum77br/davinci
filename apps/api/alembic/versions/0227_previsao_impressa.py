"""Carimbo "papel de previsão impresso" por pedido.

O botão 🖨 da aba Pedidos imprime o relatório de PREVISÃO (papel 10×15 de
separação antecipada). Eduardo (2026-08-26): "quando a gente imprimir, ja
aparecer no davinci que aquelas previsoes ja foram impressas" — o pessoal
precisa ver na tela o que já saiu no papel pra não separar duas vezes.

Chaveada por `pedido_bling` (bling_orders.numero — o grão do relatório é o
pedido, não o item), igual à nf_etiqueta_arquivo. Reimprimir só re-carimba
`impressa_em`. Sem FK: bling_orders não tem UNIQUE em numero (uma linha por
item) e o carimbo é inofensivo se o pedido sumir.
"""

import sqlalchemy as sa

from alembic import op

revision: str = "0227_previsao_impressa"
down_revision: str | None = "0226_nf_produto_cadastros"
branch_labels = None
depends_on = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.create_table(
        "previsao_impressa",
        sa.Column("pedido_bling", sa.Text(), primary_key=True),
        sa.Column(
            "impressa_em",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("previsao_impressa", schema=SCHEMA)
