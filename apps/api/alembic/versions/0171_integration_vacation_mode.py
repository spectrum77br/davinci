# ruff: noqa: E501
"""integration vacation_mode: pausa o push de estoque por integração

Coluna booleana `vacation_mode` em `integrations`. Quando True, o
SyncOrchestrator NÃO empurra estoque pra conta do marketplace (freeze — o
anúncio mantém o último estoque enviado). Só afeta estoque; preços, pedidos,
ads e OAuth seguem normais. Default false pra não mudar o comportamento das
integrações existentes.

Revision ID: 0171_integration_vacation_mode
Revises: 0170_company_certificates
Create Date: 2026-07-06
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0171_integration_vacation_mode"
down_revision: str | None = "0170_company_certificates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column(
        "integrations",
        sa.Column(
            "vacation_mode",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("integrations", "vacation_mode", schema=SCHEMA)
