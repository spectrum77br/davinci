# ruff: noqa: E501, S608
"""vw_bling_pedidos: source segment via pricing_products instead of products

The previous join (LEFT JOIN products p ON lower(p.sku) = lower(item_codigo))
hit the Bling-sync products table, where segment_id is mostly NULL — only
1.66% of bling order lines resolved a segment. Switch to pricing_products,
which carries segment_id on every row, and use a LATERAL pick that splits
pricing_products.sku on commas and matches the Bling item_codigo against
each variant exactly OR as the prefix of a `.suffix` / `+kit` SKU.

Revision ID: 0042_vw_pricing_join
Revises: 0041_auto_import_link_enum
Create Date: 2026-05-15
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0042_vw_pricing_join"
down_revision: str | None = "0041_auto_import_link_enum"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
VIEW_NAME = "vw_bling_pedidos"


# Replace the trailing `LEFT JOIN products … LEFT JOIN segments leaf …` block
# with a LATERAL that walks pricing_products. Everything else in the view
# stays identical — we keep the CTEs (order_totals / proportional_values /
# with_margin) and the column list, only the segment-resolution joins change.
OLD_TAIL = (
    "     LEFT JOIN products p ON lower(p.sku) = lower(wm.item_codigo)\n"
    "     LEFT JOIN segments leaf ON leaf.id = p.segment_id\n"
    "     LEFT JOIN segments root ON root.id = leaf.parent_id"
)

NEW_TAIL = (
    "     LEFT JOIN LATERAL (\n"
    "         SELECT pp.segment_id\n"
    "         FROM davinci.pricing_products pp,\n"
    "              regexp_split_to_table(pp.sku, ',') AS variant_raw\n"
    "         WHERE LENGTH(TRIM(variant_raw)) > 0\n"
    "           AND (\n"
    "               LOWER(wm.item_codigo) = LOWER(TRIM(variant_raw))\n"
    "               OR LOWER(wm.item_codigo) LIKE LOWER(TRIM(variant_raw)) || '.%'\n"
    "               OR LOWER(wm.item_codigo) LIKE LOWER(TRIM(variant_raw)) || '+%'\n"
    "           )\n"
    "         ORDER BY\n"
    "             CASE WHEN LOWER(wm.item_codigo) = LOWER(TRIM(variant_raw)) THEN 1\n"
    "                  WHEN LOWER(wm.item_codigo) LIKE LOWER(TRIM(variant_raw)) || '+%' THEN 2\n"
    "                  ELSE 3\n"
    "             END\n"
    "         LIMIT 1\n"
    "     ) pp ON TRUE\n"
    "     LEFT JOIN davinci.segments leaf ON leaf.id = pp.segment_id\n"
    "     LEFT JOIN davinci.segments root ON root.id = leaf.parent_id"
)

# Also need to swap the column reference `p.segment_id` → `pp.segment_id`
# in the projected `subtype_id` column.
OLD_SUBTYPE_COL = "    p.segment_id AS subtype_id,"
NEW_SUBTYPE_COL = "    pp.segment_id AS subtype_id,"


def _fetch_view_def(conn) -> str:
    from sqlalchemy import text

    # SCHEMA + VIEW_NAME are module constants, not user input — safe to inline.
    return conn.execute(
        text(f"SELECT pg_get_viewdef('{SCHEMA}.{VIEW_NAME}'::regclass, true)")
    ).scalar_one()


def _replace_once(haystack: str, needle: str, replacement: str) -> str:
    if needle not in haystack:
        raise RuntimeError(
            f"migration 0042 expected to find this exact snippet in vw_bling_pedidos "
            f"but didn't:\n---\n{needle}\n---\nactual view definition:\n{haystack}"
        )
    return haystack.replace(needle, replacement, 1)


def upgrade() -> None:
    conn = op.get_bind()
    current = _fetch_view_def(conn)
    new_def = _replace_once(current, OLD_SUBTYPE_COL, NEW_SUBTYPE_COL)
    new_def = _replace_once(new_def, OLD_TAIL, NEW_TAIL)
    op.execute(f"DROP VIEW IF EXISTS {SCHEMA}.{VIEW_NAME} CASCADE")
    op.execute(f"CREATE VIEW {SCHEMA}.{VIEW_NAME} AS {new_def}")


def downgrade() -> None:
    conn = op.get_bind()
    current = _fetch_view_def(conn)
    new_def = _replace_once(current, NEW_SUBTYPE_COL, OLD_SUBTYPE_COL)
    new_def = _replace_once(new_def, NEW_TAIL, OLD_TAIL)
    op.execute(f"DROP VIEW IF EXISTS {SCHEMA}.{VIEW_NAME} CASCADE")
    op.execute(f"CREATE VIEW {SCHEMA}.{VIEW_NAME} AS {new_def}")
