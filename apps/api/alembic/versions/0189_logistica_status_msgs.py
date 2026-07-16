# ruff: noqa: E501
"""logistica_status: add status_atual + mensagem_bling + mensagem_threema

Três campos novos na aba Status (cadastro/referência):
  * `status_atual`  — o status que se identifica hoje no Bling (ANTES de
    alterar_status_bling); mesmo domínio dos nomes de situação do Bling.
  * `mensagem_bling` — texto a colar no Bling (DEPOIS de mensagem_chamado).
  * `mensagem_threema` — mensagem a enviar às pessoas notificando o problema.

Todos Text nullable — cadastro manual, sem automação por enquanto.

Revision ID: 0189_logistica_status_msgs
Revises: 0188_logistica_divergencia
Create Date: 2026-07-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0189_logistica_status_msgs"
down_revision: str | None = "0188_logistica_divergencia"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column(
        "logistica_status",
        sa.Column("status_atual", sa.Text(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "logistica_status",
        sa.Column("mensagem_bling", sa.Text(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "logistica_status",
        sa.Column("mensagem_threema", sa.Text(), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("logistica_status", "mensagem_threema", schema=SCHEMA)
    op.drop_column("logistica_status", "mensagem_bling", schema=SCHEMA)
    op.drop_column("logistica_status", "status_atual", schema=SCHEMA)
