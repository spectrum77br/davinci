"""controle de estoque: users.stock_tag (single) -> users.stock_tags (JSONB array)

Revision ID: 0082_user_stock_tags_multi
Revises: 0081_merge_controle_estoque_views
Create Date: 2026-05-22

Operators are now multi-tagged (e.g. one user can cover ci AND ra
shelves). Replaces the single-value `stock_tag` column with a JSONB
array. Backfill: each existing non-null/non-empty stock_tag becomes a
1-element array `[tag]`. The old column is dropped after backfill —
clean break, no compat shim. Code in the same commit updates every
read site to consume the list shape.

Tag vocabulary (UI labels in parens) — only the suffix-mapped ones
actually filter today; mala/eletro/insumos are listed for the UI but
return empty until we wire a Bling-tags lookup:
  ci (CI) | pi (PI) | ra (RA) | sa (SA) | sp (SP)
  us (Usados) | cd (Centro de Distribuição) | fake (Fake)
  mala (Mala) | eletro (Eletro) | insumos (Insumos)
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0082_user_stock_tags_multi"
down_revision: str | None = "0081_merge_controle_estoque_views"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("stock_tags", postgresql.JSONB(), nullable=True),
        schema=SCHEMA,
    )
    # Backfill: jsonb_build_array() turns each non-empty stock_tag into
    # a single-element array. Empty / null source rows stay null.
    op.execute(
        f"""
        UPDATE {SCHEMA}.users
           SET stock_tags = jsonb_build_array(stock_tag)
         WHERE stock_tag IS NOT NULL AND stock_tag <> ''
        """
    )
    op.drop_column("users", "stock_tag", schema=SCHEMA)


def downgrade() -> None:
    op.add_column(
        "users",
        sa.Column("stock_tag", sa.String(16), nullable=True),
        schema=SCHEMA,
    )
    op.execute(
        f"""
        UPDATE {SCHEMA}.users
           SET stock_tag = stock_tags->>0
         WHERE stock_tags IS NOT NULL
           AND jsonb_typeof(stock_tags) = 'array'
           AND jsonb_array_length(stock_tags) > 0
        """
    )
    op.drop_column("users", "stock_tags", schema=SCHEMA)
