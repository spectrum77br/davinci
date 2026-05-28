"""devolution troca/estoque modal fields

Revision ID: 0104
Revises: 0103
Create Date: 2026-05-28

Adiciona 3 colunas que guardam as escolhas feitas nos modais de devolução
antes de chamar o estoque/produtos do Bling:

  * troca_sku       — SKU escolhido quando a condição é "Trocado" (o item
                      que de fato voltou ao estoque, distinto do `sku`
                      vendido que o Bling registra no pedido).
  * troca_condicao  — "Novo"/"Usado" escolhido no modal de troca.
  * estoque_suffix  — sufixo escolhido no modal de SKU terminado em `.sp`
                      (ex.: "ra", "pi"), redirecionando a entrada de estoque.
"""

from alembic import op
import sqlalchemy as sa

revision = "0104_devolution_troca_fields"
down_revision = "0103_devolver_estoque_bool"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("devolutions", sa.Column("troca_sku", sa.Text(), nullable=True))
    op.add_column("devolutions", sa.Column("troca_condicao", sa.Text(), nullable=True))
    op.add_column("devolutions", sa.Column("estoque_suffix", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("devolutions", "estoque_suffix")
    op.drop_column("devolutions", "troca_condicao")
    op.drop_column("devolutions", "troca_sku")
