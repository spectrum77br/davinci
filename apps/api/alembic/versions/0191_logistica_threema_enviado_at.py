# ruff: noqa: E501
"""logistica: add threema_enviado_at (marca que o aviso Threema já foi enviado)

Quando o operador dispara a Mensagem Threema por linha do marketplace, carimba
o instante aqui. A partir daí a regra casada deixa de contar a Mensagem Threema
como pendência → o pedido é considerado resolvido e some do painel (o que tinha
que ser feito já foi feito).

TIMESTAMPTZ nullable — NULL = ainda não enviado.

Revision ID: 0191_logistica_threema_enviado_at
Revises: 0190_logistica_status_threema_recipients
Create Date: 2026-07-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0191_logistica_threema_enviado_at"
down_revision: str | None = "0190_logistica_status_threema_recipients"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column(
        "logistica",
        sa.Column("threema_enviado_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("logistica", "threema_enviado_at", schema=SCHEMA)
