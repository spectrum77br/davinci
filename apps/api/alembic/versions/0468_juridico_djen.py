"""Jurídico: movimentações lidas do Diário de Justiça Eletrônico Nacional (DJEN)

Vinicius (11/11/2026): "vamos vincular para deixar ele atualizando os últimos passos
sozinho". O Vigia do Jurídico lê todo dia no DJEN (CNJ) as publicações de cada processo com
nº e lança no histórico. Colunas novas na movimentação: o id da comunicação no DJEN (não
lança duas vezes), o órgão, o tipo, o texto inteiro e o link do documento.

Revision ID: 0468_juridico_djen
Revises: 0467_juridico_ref_caso_sem_ajuizado
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0468_juridico_djen"
down_revision: str | None = "0467_juridico_ref_caso_sem_ajuizado"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
TABELA = "juridico_movimentacoes"
COLUNAS = ("externo_id", "orgao", "tipo", "texto_completo", "link")


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    for c in COLUNAS:
        op.add_column(TABELA, sa.Column(c, sa.Text(), nullable=True), schema=SCHEMA)
    op.create_index(
        "uq_juridico_movimentacoes_externo",
        TABELA,
        ["processo_id", "externo_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("externo_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_juridico_movimentacoes_externo", table_name=TABELA, schema=SCHEMA)
    for c in reversed(COLUNAS):
        op.drop_column(TABELA, c, schema=SCHEMA)
