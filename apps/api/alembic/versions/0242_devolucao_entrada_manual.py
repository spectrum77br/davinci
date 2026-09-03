# ruff: noqa: E501
"""Acompanhamento: "Em devolução desde" editável na mão — Eduardo, 2026-09-03.

"por exemplo esse aqui está aparecendo um dia mas a data está 19/08" (287144:
entrou em Aguardando Devolução em 19/08 pela Viena, no Bling; o backfill da
0236 carimbou 02/09 e o Bling não expõe o histórico pela API). Quando nem o
marketplace nem a Logística têm a data, o operador preenche — e o que ele
digitar vale mais que o automático (mesma regra do rastreio/localização).

Revision ID: 0242_devolucao_entrada_manual
Revises: 0241_devolucao_rastreio_auto
Create Date: 2026-09-03
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0242_devolucao_entrada_manual"
down_revision: str | None = "0241_devolucao_rastreio_auto"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.add_column("devolucao_rastreio", sa.Column("entrada_manual", sa.Date(), nullable=True))


def downgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.drop_column("devolucao_rastreio", "entrada_manual")
