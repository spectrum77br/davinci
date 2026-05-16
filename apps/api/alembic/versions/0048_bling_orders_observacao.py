# ruff: noqa: E501
"""add observacao column to bling_orders

Revision ID: 0048_bling_orders_observacao
Revises: 0047_vw_conciliacao_margens_frete_projetado
Create Date: 2026-05-16
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0048_bling_orders_observacao"
down_revision: str | None = "0047_vw_conciliacao_margens_frete_projetado"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column(
        "bling_orders",
        sa.Column("observacao", sa.Text(), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("bling_orders", "observacao", schema=SCHEMA)
