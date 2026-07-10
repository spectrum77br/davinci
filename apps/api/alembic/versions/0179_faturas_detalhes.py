# ruff: noqa: E501
"""rename faturas.dominios -> detalhes

O campo servia pra qualquer detalhe/item da assinatura (domínios do
hostinguer, apps, contas), não só "domínios". Renomeia a coluna pra um nome
genérico; os dados (um item por linha / separados por " / ") são preservados.

Revision ID: 0179_faturas_detalhes
Revises: 0178_faturas_dominios
Create Date: 2026-07-10
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0179_faturas_detalhes"
down_revision: str | None = "0178_faturas_dominios"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.alter_column(
        "faturas",
        "dominios",
        new_column_name="detalhes",
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.alter_column(
        "faturas",
        "detalhes",
        new_column_name="dominios",
        schema=SCHEMA,
    )
