"""Jurídico: leituras do Diário oficial (Mac mini e botão); sai o robô "Vigia do Jurídico"

O DJEN responde 403 ao servidor de produção (fora do Brasil). Vinicius (11/11/2026): "faz nos
dois, no Mac mini, e também um botão ali onde tá Atualizar". Quem consulta o Diário passa a ser
o Mac mini da Makisa (todo dia às 07:30) e o navegador (botão "Buscar no Diário"). Cada leitura
fica registrada aqui e o topo do painel Jurídico mostra a última.

O robô "vigia_juridico" sai da Ouvidoria: lá só entra o que o servidor roda. As rodadas e
ocorrências dele, se houver, vão junto (FK com CASCADE).

Revision ID: 0469_juridico_leituras
Revises: 0468_juridico_djen
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0469_juridico_leituras"
down_revision: str | None = "0468_juridico_djen"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.create_table(
        "juridico_leituras",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "quando", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("origem", sa.Text(), nullable=False),
        sa.Column("por", sa.Text(), nullable=True),
        sa.Column("processos", sa.Integer(), server_default="0", nullable=False),
        sa.Column("publicacoes", sa.Integer(), server_default="0", nullable=False),
        sa.Column("novas", sa.Integer(), server_default="0", nullable=False),
        sa.Column("falhas", sa.Integer(), server_default="0", nullable=False),
        sa.Column("erro", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_juridico_leituras")),
        schema=SCHEMA,
    )
    op.execute("DELETE FROM davinci.ouvidoria_robos WHERE chave = 'vigia_juridico'")


def downgrade() -> None:
    op.drop_table("juridico_leituras", schema=SCHEMA)
