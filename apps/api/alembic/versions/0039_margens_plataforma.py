# ruff: noqa: S608
"""add plataforma to margens

Revision ID: 0039_margens_plataforma
Revises: 0038_lucro_marketplace
Create Date: 2026-05-13
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0039_margens_plataforma"
down_revision: str | None = "0038_lucro_marketplace"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column("margens", sa.Column("plataforma", sa.Text(), nullable=True), schema=SCHEMA)

    op.execute(
        f"""
        WITH src AS (
            SELECT DISTINCT ON (numero, LOWER(item_codigo))
                   numero,
                   LOWER(item_codigo) AS sku_lc,
                   marketplace AS plataforma
              FROM {SCHEMA}.vw_bling_pedidos
             WHERE marketplace IS NOT NULL
             ORDER BY numero, LOWER(item_codigo), bo_created_at DESC NULLS LAST
        )
        UPDATE {SCHEMA}.margens m
           SET plataforma = src.plataforma
          FROM src
         WHERE m.pedido_bling::text = src.numero
           AND LOWER(COALESCE(m.sku, '')) = src.sku_lc
           AND m.plataforma IS DISTINCT FROM src.plataforma
        """
    )


def downgrade() -> None:
    op.drop_column("margens", "plataforma", schema=SCHEMA)
