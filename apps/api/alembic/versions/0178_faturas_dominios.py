# ruff: noqa: E501
"""add dominios column to faturas + split existing entries

Separa o provedor (ex.: "hostinguer") da lista de domínios. Registros legados
guardavam tudo num só campo no padrão "hostinguer dominios: a.com / b.com" —
o backfill move a parte após "dominios:" pra coluna nova e deixa só o provedor
em `servico`.

Revision ID: 0178_faturas_dominios
Revises: 0177_automacoes
Create Date: 2026-07-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0178_faturas_dominios"
down_revision: str | None = "0177_automacoes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column(
        "faturas",
        sa.Column("dominios", sa.Text(), nullable=True),
        schema=SCHEMA,
    )
    # Backfill: para "<provedor> dominios: <lista>", extrai a lista pra
    # `dominios` e deixa `<provedor>` em `servico`. Ambas as expressões usam o
    # valor ANTIGO de servico (Postgres avalia o RHS com os valores da linha
    # antes do UPDATE). Cobre "dominios"/"domínios" (case-insensitive).
    op.execute(
        r"""
        UPDATE davinci.faturas
        SET
            dominios = NULLIF(trim(regexp_replace(servico, '^.*?dom[ií]nios:\s*', '', 'i')), ''),
            servico  = trim(regexp_replace(servico, '\s*dom[ií]nios:.*$', '', 'i'))
        WHERE servico ~* 'dom[ií]nios:'
        """
    )


def downgrade() -> None:
    op.drop_column("faturas", "dominios", schema=SCHEMA)
