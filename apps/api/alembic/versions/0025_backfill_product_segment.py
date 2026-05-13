"""backfill product.segment_id from existing pricing_products (department + product_type)

For each pricing_product, finds matching products via the same SKU-piece logic
the audit/stock-map already uses (celular: dot-variant; mala/eletro/catalogo:
exact case-insensitive), and sets `product.segment_id` to the leaf segment
where:
    segment.parent.slug = pricing_product.department
    segment.sort_order  = pricing_product.product_type - 1

Idempotent. Rerunning won't change rows already pointing at the same leaf.

Revision ID: 0025_backfill_product_segment
Revises: 0024_segments_subtypes_seed
Create Date: 2026-05-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025_backfill_product_segment"
down_revision: str | None = "0024_segments_subtypes_seed"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def _dept_value(d: object) -> str:
    return d.value if hasattr(d, "value") else str(d)


def upgrade() -> None:
    bind = op.get_bind()

    # ---------------------------------------------------------- segment lookup
    # (dept_slug, sort_order) -> leaf segment id
    rows = bind.execute(
        sa.text(
            f"""
            SELECT r.slug AS root_slug, s.sort_order, s.id
            FROM {SCHEMA}.segments s
            JOIN {SCHEMA}.segments r ON r.id = s.parent_id
            WHERE r.parent_id IS NULL
            """
        )
    ).all()
    leaf_by_key: dict[tuple[str, int], str] = {
        (r.root_slug, int(r.sort_order)): str(r.id) for r in rows
    }
    if not leaf_by_key:
        return

    # ---------------------------------------------------------- product index
    prod_rows = bind.execute(
        sa.text(
            f"SELECT id, LOWER(sku) AS sku_l FROM {SCHEMA}.products "
            "WHERE sku IS NOT NULL AND sku != ''"
        )
    ).all()
    # Lowercased sku → id
    by_exact: dict[str, list[str]] = {}
    # Celular base (split on '.') → ids
    by_base: dict[str, list[str]] = {}
    for r in prod_rows:
        skl = r.sku_l.strip()
        if not skl:
            continue
        by_exact.setdefault(skl, []).append(str(r.id))
        base = skl.split(".")[0]
        if base:
            by_base.setdefault(base, []).append(str(r.id))

    # ---------------------------------------------------------- pricing_products
    pp_rows = bind.execute(
        sa.text(
            f"""
            SELECT id, department::text AS department, product_type, sku
            FROM {SCHEMA}.pricing_products
            ORDER BY department, product_type
            """
        )
    ).all()

    # Resolve each pricing_product -> set of matched product ids
    updates: dict[str, str] = {}  # product_id -> segment_id
    for pp in pp_rows:
        dept = (pp.department or "").lower()
        pt = int(pp.product_type or 0)
        if pt < 1 or pt > 5:
            continue
        leaf_id = leaf_by_key.get((dept, pt - 1))
        if leaf_id is None:
            continue

        for piece in (pp.sku or "").split(","):
            key = piece.strip().lower()
            if not key:
                continue
            if dept == "catalogo" and "+" in key:
                continue
            matched_ids: list[str] = []
            if dept == "celular":
                matched_ids = by_base.get(key, [])
            else:
                matched_ids = by_exact.get(key, [])
            for pid in matched_ids:
                # First pricing_product that matches a product wins (deterministic
                # since pp_rows are ordered).
                updates.setdefault(pid, leaf_id)

    # ---------------------------------------------------------- apply updates
    # Only write where current segment_id is NULL or differs.
    if updates:
        # Bulk update in chunks of 500 to keep statements small.
        items = list(updates.items())
        for i in range(0, len(items), 500):
            chunk = items[i : i + 500]
            # Build a VALUES table and UPDATE join.
            values_sql = ", ".join(
                f"('{pid}'::uuid, '{sid}'::uuid)" for pid, sid in chunk
            )
            bind.execute(
                sa.text(
                    f"""
                    UPDATE {SCHEMA}.products p
                    SET segment_id = v.segment_id
                    FROM (VALUES {values_sql}) AS v(product_id, segment_id)
                    WHERE p.id = v.product_id
                      AND (p.segment_id IS DISTINCT FROM v.segment_id)
                    """
                )
            )


def downgrade() -> None:
    # No-op: we cannot reliably tell which segment_ids were set by this
    # backfill vs. by user UI clicks. Leaving them in place is safe.
    pass
