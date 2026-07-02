# ruff: noqa: E501, S608
"""companies.operacao / companies.contabilidade: dois campos texto livres

A página de Empresas ganhou duas colunas editáveis inline (como `obs`):
"operação" e "contabilidade" — texto livre pra registrar quem/como cuida da
operação e da contabilidade de cada empresa. Ambas nullable, sem default.

Idempotente (checa information_schema antes do add_column) pra ser seguro
rodar em cima de um banco onde a coluna já exista.

Revision ID: 0169_companies_operacao_contabilidade
Revises: 0168_sync_logs_product_link_id_idx
Create Date: 2026-07-02
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0169_companies_operacao_contabilidade"
down_revision: str | None = "0168_sync_logs_product_link_id_idx"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
_COLS = ("operacao", "contabilidade")


def upgrade() -> None:
    bind = op.get_bind()
    for col in _COLS:
        has_col = bind.execute(
            sa.text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = :s AND table_name = 'companies' "
                "AND column_name = :c"
            ),
            {"s": SCHEMA, "c": col},
        ).first()
        if not has_col:
            op.add_column(
                "companies",
                sa.Column(col, sa.Text(), nullable=True),
                schema=SCHEMA,
            )


def downgrade() -> None:
    for col in _COLS:
        op.drop_column("companies", col, schema=SCHEMA)
