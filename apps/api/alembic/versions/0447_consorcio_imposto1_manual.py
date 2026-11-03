"""Consórcio — coluna imposto1_manual (Imposto 1% editável, 03/11/2026)

Eduardo, 03/11: "criar uma nova coluna de imposto 1%, e esse valor de 1%
desconta do crédito líquido" (escolheu: em todas as cotas com lance, igual ao
Imposto 7%) e "na coluna de 1% deixe a opção de editar caso eu precise deixar
zerado". O 1% é calculado na tela; esta coluna guarda só o valor digitado.
NULL = automático (1% do crédito); 0 = zerado à mão. Nenhuma linha muda: todas
começam no automático.

DOWNGRADE: apaga a coluna e os valores digitados nela.

DEPLOY: a API nova lê a coluna — rodar a migration ANTES do `up -d` (a API de
antes não liga para a coluna a mais).

Revision ID: 0447_consorcio_imposto1_manual
Revises: 0446_conciliacao_conta_na_venda
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0447_consorcio_imposto1_manual"
down_revision: str | None = "0446_conciliacao_conta_na_venda"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Tabela pequena, mas o ADD COLUMN tranca tudo: não esperar atrás de leitura longa.
    op.execute("SET LOCAL lock_timeout = '30s'")
    op.add_column(
        "financeiro_consorcio",
        sa.Column("imposto1_manual", sa.Numeric(14, 2), nullable=True),
        schema="davinci",
    )


def downgrade() -> None:
    op.drop_column("financeiro_consorcio", "imposto1_manual", schema="davinci")
