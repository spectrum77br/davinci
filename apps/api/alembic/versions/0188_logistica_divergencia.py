# ruff: noqa: E501
"""logistica: add divergencia

Coluna que explica em texto a divergência entre o status do Mercado Livre e o
rastreio físico dos Correios (ex.: Correios consta entregue mas o ML consta
não entregue/cancelado). Auto-calculada; vazia quando batem.

Revision ID: 0188_logistica_divergencia
Revises: 0187_logistica_status_anexo
Create Date: 2026-07-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0188_logistica_divergencia"
down_revision: str | None = "0187_logistica_status_anexo"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column(
        "logistica",
        sa.Column("divergencia", sa.Text(), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("logistica", "divergencia", schema=SCHEMA)
