# ruff: noqa: E501, S608
"""add margens.margem_min, drop approved rows, backfill from vw_bling_pedidos

Revision ID: 0034_margens_margem_min
Revises: 0033_bling_orders_approval_status
Create Date: 2026-05-13
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0034_margens_margem_min"
down_revision: str | None = "0033_bling_orders_approval_status"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column(
        "margens",
        sa.Column("margem_min", sa.Float(), nullable=True),
        schema=SCHEMA,
    )

    op.execute(f"DELETE FROM {SCHEMA}.margens WHERE status ILIKE 'aprovado'")

    op.execute(
        f"""
        UPDATE {SCHEMA}.margens m
           SET margem_min = v.min_margin
          FROM (
            SELECT DISTINCT ON (numero, LOWER(item_codigo))
                   numero, LOWER(item_codigo) AS sku_lc, min_margin
              FROM {SCHEMA}.vw_bling_pedidos
             WHERE min_margin IS NOT NULL
          ) v
         WHERE m.pedido_bling::text = v.numero
           AND LOWER(COALESCE(m.sku, '')) = v.sku_lc
        """
    )


def downgrade() -> None:
    op.drop_column("margens", "margem_min", schema=SCHEMA)
