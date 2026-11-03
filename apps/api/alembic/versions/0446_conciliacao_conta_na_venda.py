"""Conciliação — coluna conta_na_venda em conciliacao_pedidos (03/11).

Vinicius, 03/11 (Shopee vortan 306115: "previsto na venda 732,10 × previsto na
liberação 720,34", e o raio-x todo com 0,00): "o previsto e o real não era pra
ser exatamente isso?". A Margem relê o pedido quando a Shopee libera e o Previsto
virava a conta final — as duas colunas saíam sempre iguais. A coluna guarda a
conta da Shopee linha a linha (preço, subsídio, frete, cupons, taxas, afiliados,
renda) enquanto o pedido não libera e congela na liberação: o Previsto do
raio-x e da grade passa a ser o que a venda prometeu, e pedido que recebeu menos
que isso é "Recebido a menos" (escolha dele). Preenchida daqui pra frente: pedido
já liberado fica como estava.

Revision ID: 0446_conciliacao_conta_na_venda
Revises: 0445_equipe_da_loja_123
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0446_conciliacao_conta_na_venda"
down_revision: str | None = "0445_equipe_da_loja_123"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "conciliacao_pedidos",
        sa.Column("conta_na_venda", JSONB, nullable=True),
        schema="davinci",
    )


def downgrade() -> None:
    op.drop_column("conciliacao_pedidos", "conta_na_venda", schema="davinci")
