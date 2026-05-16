# ruff: noqa: E501
"""add observacao column to bling_orders

Revision ID: 0051_bling_orders_observacao
Revises: 0050_vw_conciliacao_margens_frete_projetado
Create Date: 2026-05-16
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0051_bling_orders_observacao"
down_revision: str | None = "0050_vw_conciliacao_margens_frete_projetado"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    # Idempotent: column may already exist in environments where it was
    # backfilled out-of-band before this migration was applied.
    op.execute(f'ALTER TABLE "{SCHEMA}".bling_orders ADD COLUMN IF NOT EXISTS observacao TEXT')


def downgrade() -> None:
    op.execute(f'ALTER TABLE "{SCHEMA}".bling_orders DROP COLUMN IF EXISTS observacao')
