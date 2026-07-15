# ruff: noqa: E501
"""logistica_status: funde anexar_envio em mensagem_chamado e torna campos opcionais

- Move o conteúdo de `anexar_envio` pra dentro de `mensagem_chamado` (a coluna
  passa a acumular a instrução do que anexar: foto/link/o que for) e dropa a
  coluna `anexar_envio`.
- Torna `status_plataforma` NULLABLE: o operador preenche as células à mão, então
  uma linha pode existir vazia pra ser completada depois.

Revision ID: 0185_logistica_status_optional
Revises: 0184_logistica_status_extra_cols
Create Date: 2026-07-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0185_logistica_status_optional"
down_revision: str | None = "0184_logistica_status_extra_cols"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    # Preserva o que houver em anexar_envio, concatenando em mensagem_chamado.
    op.execute(
        f"""
        UPDATE {SCHEMA}.logistica_status
        SET mensagem_chamado = NULLIF(
            concat_ws(E'\\n', NULLIF(mensagem_chamado, ''), NULLIF(anexar_envio, '')),
            ''
        )
        WHERE anexar_envio IS NOT NULL AND anexar_envio <> ''
        """
    )
    op.drop_column("logistica_status", "anexar_envio", schema=SCHEMA)
    op.alter_column(
        "logistica_status", "status_plataforma", nullable=True, schema=SCHEMA
    )


def downgrade() -> None:
    op.alter_column(
        "logistica_status", "status_plataforma", nullable=False, schema=SCHEMA
    )
    op.add_column(
        "logistica_status",
        sa.Column("anexar_envio", sa.Text(), nullable=True),
        schema=SCHEMA,
    )
