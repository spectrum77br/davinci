# ruff: noqa: E501
"""logistica_status: add threema_recipients (destinatários salvos por regra)

Guarda os Threema IDs escolhidos pra ESTA regra (separados por vírgula). O
seletor 👤 da aba Status salva quem recebe; o envio (aba Status ou por linha do
marketplace) usa essa lista por padrão. Vazio = cai na lista fixa do `.env`.

Text nullable — cadastro manual.

Revision ID: 0190_logistica_status_threema_recipients
Revises: 0189_logistica_status_msgs
Create Date: 2026-07-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0190_logistica_status_threema_recipients"
down_revision: str | None = "0189_logistica_status_msgs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column(
        "logistica_status",
        sa.Column("threema_recipients", sa.Text(), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("logistica_status", "threema_recipients", schema=SCHEMA)
