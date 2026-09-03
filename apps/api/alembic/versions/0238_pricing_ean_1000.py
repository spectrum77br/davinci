# ruff: noqa: E501
"""EAN da tabela de preços passa a caber 1000 caracteres (Eduardo, 2026-09-03).

"aumente o campo ean para caber 1000 caracteres" — na aba Produtos da
precificação uma linha agrupa várias cores (i238,i239,i240,i241) e cada cor
tem EAN próprio; com varchar(64) só cabia um código. Alarga a coluna para
varchar(1000); nenhum dado existente muda (alargar varchar é in-place no
Postgres, sem rewrite).

Revision ID: 0238_pricing_ean_1000
Revises: 0237_margem_saldo_manual
Create Date: 2026-09-03
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0238_pricing_ean_1000"
down_revision: str | None = "0237_margem_saldo_manual"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.alter_column(
        "pricing_products",
        "ean",
        existing_type=sa.String(64),
        type_=sa.String(1000),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    # Trunca antes de estreitar para o ALTER não falhar em valores longos.
    op.execute("UPDATE pricing_products SET ean = LEFT(ean, 64) WHERE LENGTH(ean) > 64")
    op.alter_column(
        "pricing_products",
        "ean",
        existing_type=sa.String(1000),
        type_=sa.String(64),
        existing_nullable=True,
    )
