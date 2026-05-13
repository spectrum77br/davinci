# ruff: noqa: S608
"""backfill margens.conta with loja nome

Revision ID: 0037_margens_conta_loja_nome
Revises: 0036_vw_bling_store_join
Create Date: 2026-05-13
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0037_margens_conta_loja_nome"
down_revision: str | None = "0036_vw_bling_store_join"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute(
        f"""
        WITH src AS (
            SELECT DISTINCT ON (numero, LOWER(item_codigo))
                   numero,
                   LOWER(item_codigo) AS sku_lc,
                   loja_nome
              FROM {SCHEMA}.vw_bling_pedidos
             WHERE loja_nome IS NOT NULL
             ORDER BY numero, LOWER(item_codigo), bo_created_at DESC NULLS LAST
        )
        UPDATE {SCHEMA}.margens m
           SET conta = src.loja_nome
          FROM src
         WHERE m.pedido_bling::text = src.numero
           AND LOWER(COALESCE(m.sku, '')) = src.sku_lc
           AND m.conta IS DISTINCT FROM src.loja_nome
        """
    )


def downgrade() -> None:
    pass
