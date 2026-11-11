"""Jurídico: a última tentativa do robô no Jusbrasil, por processo

Vinicius (11/11/2026): "como vou saber, se ele tentou atualizar o caso 9 e não conseguiu?" —
na tabela, coluna Jusbrasil. O robô do Mac mini grava a hora da última tentativa e, se falhou
(não achou o processo na busca, a página não abriu…), o motivo.

Revision ID: 0471_juridico_jusbrasil_tentativa
Revises: 0470_juridico_pedidos
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0471_juridico_jusbrasil_tentativa"
down_revision: str | None = "0470_juridico_pedidos"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.add_column(
        "juridico_processos",
        sa.Column("jusbrasil_tentado_em", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "juridico_processos", sa.Column("jusbrasil_erro", sa.Text(), nullable=True), schema=SCHEMA
    )


def downgrade() -> None:
    op.drop_column("juridico_processos", "jusbrasil_erro", schema=SCHEMA)
    op.drop_column("juridico_processos", "jusbrasil_tentado_em", schema=SCHEMA)
