# ruff: noqa: E501
"""Chamados: coluna "Valor" (valor recuperado com o chamado) — Eduardo, 2026-09-03.

"em chamados, vamos adicionar uma coluna nova em campo controle com a aba
chamada valor, que é o valor que conseguimos recuperar com o chamado".
Numérico (R$), preenchido na mão na aba Chamados; NULL = ainda sem valor.

Revision ID: 0240_chamados_valor_recuperado
Revises: 0239_devolucao_motivo_bloqueado
Create Date: 2026-09-03
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0240_chamados_valor_recuperado"
down_revision: str | None = "0239_devolucao_motivo_bloqueado"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.add_column(
        "chamados",
        sa.Column("valor_recuperado", sa.Numeric(12, 2), nullable=True),
    )


def downgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.drop_column("chamados", "valor_recuperado")
