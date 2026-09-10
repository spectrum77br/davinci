# ruff: noqa: E501
"""Logística: quando o rastreio foi CONSULTADO (não quando mudou).

Eduardo, 10/09/2026: "parou de atualizar o rastreamento dos correios nos
horários? pois não atualizou". Não tinha parado — o 17track leu os Correios às
10h40 daquele dia e não havia evento novo. O que enganava era a tela: ela
escrevia "Correios · lido há 22 h" usando `localizacao_at`, que só é carimbado
quando a localização MUDA. Sem evento novo, o carimbo envelhece e parece que o
sistema desistiu.

Revision ID: 0256_logistica_rastreio_lido_em
Revises: 0255_devolucao_observacao
Create Date: 2026-09-10
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0256_logistica_rastreio_lido_em"
down_revision: str | None = "0255_devolucao_observacao"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.add_column(
        "logistica",
        sa.Column("rastreio_lido_em", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.drop_column("logistica", "rastreio_lido_em")
