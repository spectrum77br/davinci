# ruff: noqa: E501, S608
"""store_info.archived_at / integrations.archived_at: arquivar loja suspensa

A página Lojas ganhou o botão "Arquivar": ao arquivar uma loja (conta de
marketplace suspensa), ela some de Lojas, da Tabela de Preço e de Produtos, e
o sync para de empurrar estoque/preço pra ela. "Ativar" reverte. O estado é um
timestamp `archived_at` (NULL = ativa) em `store_info` (governa a tela Lojas) e
em `integrations` (governa Produtos + sync). O endpoint de arquivar propaga da
loja pra integração vinculada.

Idempotente (checa information_schema antes do add_column).

Revision ID: 0170_store_info_integration_archived_at
Revises: 0169_companies_operacao_contabilidade
Create Date: 2026-07-07
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0170_store_info_integration_archived_at"
down_revision: str | None = "0169_companies_operacao_contabilidade"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
_TABLES = ("store_info", "integrations")


def _has_col(bind, table: str, col: str) -> bool:
    return bind.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = :s AND table_name = :t AND column_name = :c"
        ),
        {"s": SCHEMA, "t": table, "c": col},
    ).first() is not None


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        if not _has_col(bind, table, "archived_at"):
            op.add_column(
                table,
                sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
                schema=SCHEMA,
            )


def downgrade() -> None:
    for table in _TABLES:
        op.drop_column(table, "archived_at", schema=SCHEMA)
