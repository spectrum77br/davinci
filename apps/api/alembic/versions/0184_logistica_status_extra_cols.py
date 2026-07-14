# ruff: noqa: E501
"""logistica_status: colunas plataforma, monitoramento, anexar_envio

Adiciona à aba Status (tabela `logistica_status`) os campos que faltavam pra
espelhar a referência da planilha: `plataforma` (marketplace da regra),
`monitoramento` (flag) e `anexar_envio` (instrução do que anexar no envio).

Revision ID: 0184_logistica_status_extra_cols
Revises: 0183_stock_check_saldo
Create Date: 2026-07-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0184_logistica_status_extra_cols"
down_revision: str | None = "0183_stock_check_saldo"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column("logistica_status", sa.Column("plataforma", sa.Text(), nullable=True), schema=SCHEMA)
    op.add_column(
        "logistica_status",
        sa.Column("monitoramento", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        schema=SCHEMA,
    )
    op.add_column("logistica_status", sa.Column("anexar_envio", sa.Text(), nullable=True), schema=SCHEMA)


def downgrade() -> None:
    op.drop_column("logistica_status", "anexar_envio", schema=SCHEMA)
    op.drop_column("logistica_status", "monitoramento", schema=SCHEMA)
    op.drop_column("logistica_status", "plataforma", schema=SCHEMA)
