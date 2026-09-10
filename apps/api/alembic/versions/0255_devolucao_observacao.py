# ruff: noqa: E501
"""Devoluções: campo Observação na aba Acompanhamento.

Pedido de 10/09/2026: quem acompanha a devolução precisa de um lugar pra
anotar informação importante sobre o pacote — por PEDIDO, ao lado da data da
última movimentação — sem esperar o lançamento. A Observação da aba
Lançamentos só existe depois que a devolução é lançada; esta vive em
`devolucao_rastreio`, junto do rastreio/localização manuais.

Revision ID: 0255_devolucao_observacao
Revises: 0254_devolucao_pacote_entregue
Create Date: 2026-09-10
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0255_devolucao_observacao"
down_revision: str | None = "0254_devolucao_pacote_entregue"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.add_column(
        "devolucao_rastreio",
        sa.Column("observacao", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.drop_column("devolucao_rastreio", "observacao")
