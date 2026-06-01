"""devolution: registra o movimento de estoque feito no Bling (p/ estorno)

Revision ID: 0115
Revises: 0114

Ao devolver o item ao estoque, o sistema cria/credita um SKU no Bling (ex.:
`z0093.mala`). Antes esse resultado só ia na resposta HTTP e se perdia. Agora
persistimos o que foi efetivamente lançado, pra poder ESTORNAR (dar baixa) caso
o operador desligue o toggle "devolver estoque" depois — ex.: item entrou como
Usado e numa verificação posterior virou Sucata, então não pode ficar vendável.

  * estoque_mov_sku         — SKU que recebeu a entrada (z0093.mala, bin
                              existente ou o próprio SKU original).
  * estoque_mov_bling_id    — produto.id no Bling que recebeu a entrada.
  * estoque_mov_action      — ação retornada (entry_existing,
                              product_created_avulso, …) — distingue bin
                              existente de produto criado.
  * estoque_mov_qty         — unidades lançadas (pra estornar a mesma qtd).
  * estoque_mov_revertido_at — carimbo do estorno (NULL = ainda em estoque).
"""

from alembic import op
import sqlalchemy as sa

revision = "0115_devolution_estoque_mov"
down_revision = "0114_bling_situacao_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("devolutions", sa.Column("estoque_mov_sku", sa.Text(), nullable=True))
    op.add_column("devolutions", sa.Column("estoque_mov_bling_id", sa.BigInteger(), nullable=True))
    op.add_column("devolutions", sa.Column("estoque_mov_action", sa.Text(), nullable=True))
    op.add_column("devolutions", sa.Column("estoque_mov_qty", sa.Integer(), nullable=True))
    op.add_column(
        "devolutions",
        sa.Column("estoque_mov_revertido_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("devolutions", "estoque_mov_revertido_at")
    op.drop_column("devolutions", "estoque_mov_qty")
    op.drop_column("devolutions", "estoque_mov_action")
    op.drop_column("devolutions", "estoque_mov_bling_id")
    op.drop_column("devolutions", "estoque_mov_sku")
