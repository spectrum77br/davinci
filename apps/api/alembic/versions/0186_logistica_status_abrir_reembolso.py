# ruff: noqa: E501
"""logistica_status: add abrir_reembolso

Nova flag booleana (paralela a abrir_chamado) pra a regra da aba Status indicar
que aquele Status Plataforma deve abrir reembolso.

Revision ID: 0186_logistica_status_abrir_reembolso
Revises: 0185_logistica_status_optional
Create Date: 2026-07-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0186_logistica_status_abrir_reembolso"
down_revision: str | None = "0185_logistica_status_optional"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column(
        "logistica_status",
        sa.Column(
            "abrir_reembolso",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("logistica_status", "abrir_reembolso", schema=SCHEMA)
