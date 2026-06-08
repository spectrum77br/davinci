"""devolution: flag booleana `manutencao` (pedido já passou em manutenção)

Revision ID: 0129_devolution_manutencao_flag
Revises: 0128_lote_item_custo_manual

Registra se a devolução já PASSOU em manutenção. Marcado automaticamente quando
uma devolução de condição "Manutenção" é devolvida ao estoque (toggle ligado /
modal Novo-Usado-Sucata concluído) — inclusive Sucata, que não credita estoque
mas mesmo assim passou pelo técnico. Histórico: é um fato (não volta a false).

Backfill: linhas de Manutenção que já voltaram ao estoque (devolver_estoque) ou
que já têm destino de manutenção escolhido (manutencao_destino) são marcadas.
"""

from alembic import op
import sqlalchemy as sa

revision = "0129_devolution_manutencao_flag"
down_revision = "0128_lote_item_custo_manual"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "devolutions",
        sa.Column(
            "manutencao",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    # Backfill do histórico: Manutenção que já passou pelo fluxo de estoque.
    op.execute(
        """
        UPDATE devolutions
           SET manutencao = true
         WHERE condicao_produto = 'Manutenção'
           AND (devolver_estoque = true OR manutencao_destino IS NOT NULL)
        """
    )


def downgrade() -> None:
    op.drop_column("devolutions", "manutencao")
