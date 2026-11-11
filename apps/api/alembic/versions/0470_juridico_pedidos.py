"""Jurídico: pedidos de leitura ao Mac mini (Diário + Jusbrasil)

Vinicius (11/11/2026): "6 da manhã … chegar e ver tudo pronto" e "no botão Atualizar,
atualizar Jusbrasil e CNJ junto". O DaVinci cria um pedido às 06:00 e no botão Atualizar; o
vigia do Mac mini pega, lê o Diário (CNJ) e o Jusbrasil (perfil 50, devagar) e conta o
progresso. O topo do painel mostra o andamento.

Revision ID: 0470_juridico_pedidos
Revises: 0469_juridico_leituras
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0470_juridico_pedidos"
down_revision: str | None = "0469_juridico_leituras"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.create_table(
        "juridico_pedidos",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "pedido_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("pedido_por", sa.Text(), nullable=True),
        sa.Column("origem", sa.Text(), nullable=False),
        sa.Column("jusbrasil", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("pego_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("progresso", sa.Text(), nullable=True),
        sa.Column("terminado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ok", sa.Boolean(), nullable=True),
        sa.Column("resultado", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_juridico_pedidos")),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("juridico_pedidos", schema=SCHEMA)
