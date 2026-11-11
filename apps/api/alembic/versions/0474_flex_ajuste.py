"""Flex: o modo, a escrita da Shopee e o pedido Flex no .sp passam a ser trocados na tela

Vinicius, 11/11/2026: só o Eduardo mexia nessas chaves (ficam no .env do
servidor, root 600) — "e se precisar tirar de onde tá e colocar onde nós
conseguimos ver?" / "pode fazer, mas ainda deixa tudo desligado". Mesmo
caminho do local de saída (0385): uma linha só, trocada na aba Flex.

- flex_ajuste (id = 1): modo, shopee_escrita e pedido_no_sp, cada um NULL =
  vale o .env. A tabela nasce VAZIA: nada muda no deploy (segue observar,
  Shopee só lendo, pedido Flex fora do .sp) até alguém trocar na tela.

Só uma tabela nova. O gatilho do Histórico entra quando o worker sobe
(historico_manutencao, run_at_startup), como na flex_local.

Revision ID: 0474_flex_ajuste
Revises: 0473_churchill_logistica_ver
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0474_flex_ajuste"
down_revision: str | None = "0473_churchill_logistica_ver"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
MODOS = ("desligado", "observar", "piloto", "ativo")


def upgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    op.create_table(
        "flex_ajuste",
        sa.Column(
            "id",
            sa.SmallInteger(),
            server_default=sa.text("1"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column("modo", sa.Text(), nullable=True),
        sa.Column("shopee_escrita", sa.Boolean(), nullable=True),
        sa.Column("pedido_no_sp", sa.Boolean(), nullable=True),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("atualizado_por", pg.UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint("id = 1", name=op.f("ck_flex_ajuste_uma_linha")),
        sa.CheckConstraint(
            "modo IN (" + ", ".join(f"'{v}'" for v in MODOS) + ")", name=op.f("ck_flex_ajuste_modo")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_flex_ajuste")),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("flex_ajuste", schema=SCHEMA)
