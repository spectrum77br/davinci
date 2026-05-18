"""backfill Bling order category names

Revision ID: 0062_backfill_bling_order_category_names
Revises: 0061_product_categories
Create Date: 2026-05-18
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0062_backfill_bling_order_category_names"
down_revision: str | None = "0061_product_categories"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
MV_NAME = "mv_conciliacao_margens_marketplace"


def upgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.execute(
        """
        UPDATE davinci.bling_orders bo
        SET categoria_nome = pc.name,
            updated_at = now()
        FROM davinci.product_categories pc
        WHERE bo.categoria_id = pc.bling_category_id
          AND (
              bo.categoria_nome IS NULL
              OR btrim(bo.categoria_nome) = ''
          )
        """
    )
    op.execute(
        """
        WITH product_matches AS (
            SELECT DISTINCT ON (bo.id)
                bo.id AS bling_order_id,
                pc.bling_category_id,
                pc.name
            FROM davinci.bling_orders bo
            JOIN davinci.products p
              ON (
                  (
                      bo.item_produto_id IS NOT NULL
                      AND p.bling_product_id = bo.item_produto_id
                  )
                  OR (
                      bo.item_codigo IS NOT NULL
                      AND lower(btrim(p.sku)) = lower(btrim(bo.item_codigo))
                  )
              )
            JOIN davinci.product_categories pc
              ON (
                  p.category = pc.bling_category_id::text
                  OR lower(btrim(p.category)) = lower(btrim(pc.name))
              )
            WHERE bo.categoria_nome IS NULL
               OR btrim(bo.categoria_nome) = ''
            ORDER BY
                bo.id,
                CASE
                    WHEN p.bling_product_id = bo.item_produto_id THEN 0
                    ELSE 1
                END
        )
        UPDATE davinci.bling_orders bo
        SET categoria_id = COALESCE(bo.categoria_id, pm.bling_category_id),
            categoria_nome = pm.name,
            updated_at = now()
        FROM product_matches pm
        WHERE bo.id = pm.bling_order_id
          AND (
              bo.categoria_nome IS NULL
              OR btrim(bo.categoria_nome) = ''
          )
        """
    )

    bind = op.get_bind()
    mv_exists = bind.execute(
        sa.text("SELECT to_regclass(:mv_name)"),
        {"mv_name": f"{SCHEMA}.{MV_NAME}"},
    ).scalar_one()
    if mv_exists is not None:
        op.execute("REFRESH MATERIALIZED VIEW davinci.mv_conciliacao_margens_marketplace")


def downgrade() -> None:
    # Data backfill is intentionally not reversed.
    pass
