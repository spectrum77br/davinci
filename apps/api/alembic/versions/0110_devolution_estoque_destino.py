"""devolution: quantidade + destino de estoque + disposição de manutenção

Revision ID: 0110
Revises: 0109

Colunas novas para o fluxo de retorno ao estoque / patch de pedido no Bling:

  * quantidade           — unidades a lançar no estoque (default 1; hoje a
                           busca expande 1 linha por unidade, mas guardamos
                           o valor pra a entrada e o estoque inicial do z).
  * estoque_destino_sku  — quando o operador escolheu um bin JÁ existente
                           (`base.<sufixo>`) no modal de estoque; a entrada
                           vai direto nesse SKU.
  * estoque_nova_tag     — quando NENHUMA variante existe e o operador
                           escolheu uma tag (`pi`, `ra`, …) pra criar um
                           produto novo `z000N.<tag>`.
  * manutencao_destino   — "Novo"/"Usado"/"Sucata" escolhido no modal quando
                           a condição é "Manutenção".
"""

from alembic import op
import sqlalchemy as sa

revision = "0110_devolution_estoque_destino"
down_revision = "0109_devolution_tag_reflect_estoque"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "devolutions",
        sa.Column("quantidade", sa.Integer(), nullable=False, server_default=sa.text("1")),
    )
    op.add_column("devolutions", sa.Column("estoque_destino_sku", sa.Text(), nullable=True))
    op.add_column("devolutions", sa.Column("estoque_nova_tag", sa.Text(), nullable=True))
    op.add_column("devolutions", sa.Column("manutencao_destino", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("devolutions", "manutencao_destino")
    op.drop_column("devolutions", "estoque_nova_tag")
    op.drop_column("devolutions", "estoque_destino_sku")
    op.drop_column("devolutions", "quantidade")
