"""Chamados: marca Encerrar na coluna Revisão

Vinicius (11/11/2026): "vamos tirar do Londres, Israel e Marrocos o botão de
encerrar os chamados. Somente eu consigo encerrar. Eles vão acompanhar tudo,
resolver e colocar como Encerrar. Daí eu confiro e encerro."

A marca nova não precisa de coluna no chamado (`revisao_tipo` é texto). A foto do
dia ganha a contagem `encerrar`; os dias antigos ficam com 0.

Revision ID: 0465_chamados_revisao_encerrar
Revises: 0464_cairo_devolucoes_mover
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0465_chamados_revisao_encerrar"
down_revision: str | None = "0464_cairo_devolucoes_mover"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.add_column(
        "chamado_revisao_dias",
        sa.Column("encerrar", sa.Integer(), nullable=False, server_default="0"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("chamado_revisao_dias", "encerrar", schema=SCHEMA)
