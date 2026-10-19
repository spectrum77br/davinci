"""Conciliação — coluna previsto_na_venda em conciliacao_pedidos (19/10).

Revisão de 19/10: a Shopee e a TikTok fecham a conta da venda na hora de liberar
(afiliados, taxa de serviço) e a Margem relê o pedido nesse momento — o previsto
novo substituía o antigo e a diferença que a plataforma acrescentou sumia (Jlas
301039: 1.604,51 na venda → 1.583,99 na liberação, "bateu"). A coluna guarda o
previsto como estava ANTES de liberar; o recálculo compara e mostra "a plataforma
mudou a conta em −20,52 na liberação". Preenchida daqui pra frente, na primeira
rodada em que o pedido aparece liberado.

Revision ID: 0417_conciliacao_previsto_na_venda
Revises: 0416_notificacoes_nativas
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0417_conciliacao_previsto_na_venda"
down_revision: str | None = "0416_notificacoes_nativas"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "conciliacao_pedidos",
        sa.Column("previsto_na_venda", sa.Numeric(14, 2), nullable=True),
        schema="davinci",
    )


def downgrade() -> None:
    op.drop_column("conciliacao_pedidos", "previsto_na_venda", schema="davinci")
