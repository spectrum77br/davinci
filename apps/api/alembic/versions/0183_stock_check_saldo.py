# ruff: noqa: E501
"""stock_checks: saldo congelado na conferência (Controle de Estoque histórico)

Adiciona `saldo_virtual` e `reserved` (ambos nullable) em `stock_checks`.
Quando o operador tica CONFERIDO na aba Estoque, gravamos aqui o saldo
daquele instante. Assim, ao abrir um dia PASSADO, a coluna Saldo mostra o
que existia naquele dia (item conferido com 0 continua 0 mesmo depois que
chega estoque) em vez do saldo ao vivo de hoje.

Colunas nullable + additivas → migração segura, sem backfill (linhas
antigas ficam NULL e caem no comportamento ao vivo).

Revision ID: 0183_stock_check_saldo
Revises: 0182_logistica
Create Date: 2026-07-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0183_stock_check_saldo"
down_revision: str | None = "0182_logistica"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column("stock_checks", sa.Column("saldo_virtual", sa.Integer(), nullable=True), schema=SCHEMA)
    op.add_column("stock_checks", sa.Column("reserved", sa.Integer(), nullable=True), schema=SCHEMA)


def downgrade() -> None:
    op.drop_column("stock_checks", "reserved", schema=SCHEMA)
    op.drop_column("stock_checks", "saldo_virtual", schema=SCHEMA)
