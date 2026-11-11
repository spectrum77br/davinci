"""Jurídico: pedido de leitura de um processo só (Atualizar da janela do processo)

Vinicius (11/11/2026), na janela do Caso 009: "tá só buscar no Diário — coloca Atualizar, que
atualize Diário e Jusbrasil". O pedido ao Mac mini ganha `processo_id`: vazio = todos os
processos com nº (06:00 e Atualizar do topo); preenchido = só aquele.

Revision ID: 0472_juridico_pedido_processo
Revises: 0471_juridico_jusbrasil_tentativa
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0472_juridico_pedido_processo"
down_revision: str | None = "0471_juridico_jusbrasil_tentativa"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.add_column(
        "juridico_pedidos", sa.Column("processo_id", sa.BigInteger(), nullable=True), schema=SCHEMA
    )


def downgrade() -> None:
    op.drop_column("juridico_pedidos", "processo_id", schema=SCHEMA)
