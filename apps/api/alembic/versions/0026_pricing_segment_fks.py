"""add segment_id FK to pricing_account + pricing_product (Phase 2a additive)

Adds nullable `segment_id` columns:
  * pricing_accounts.segment_id  → root segment (e.g. Celular)
  * pricing_products.segment_id  → leaf segment (e.g. Celular / Robusto)

Then backfills from the existing (department, product_type) pair:
  - account.segment_id = root segment whose slug = account.department
  - product.segment_id = leaf segment whose
        parent.slug = product.department AND sort_order = product_type - 1

Both columns remain nullable so legacy code paths that still read
`department` keep working until the cutover migration (Phase 2c).

Revision ID: 0026_pricing_segment_fks
Revises: 0025_backfill_product_segment
Create Date: 2026-05-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0026_pricing_segment_fks"
down_revision: str | None = "0025_backfill_product_segment"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column(
        "pricing_accounts",
        sa.Column(
            "segment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.segments.id", ondelete="SET NULL"),
            nullable=True,
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_pricing_accounts_segment_id",
        "pricing_accounts",
        ["segment_id"],
        schema=SCHEMA,
    )

    op.add_column(
        "pricing_products",
        sa.Column(
            "segment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.segments.id", ondelete="SET NULL"),
            nullable=True,
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_pricing_products_segment_id",
        "pricing_products",
        ["segment_id"],
        schema=SCHEMA,
    )

    bind = op.get_bind()

    # ---------------------------------------------------------- account backfill
    # Each account → root segment matching account.department::text
    bind.execute(
        sa.text(
            f"""
            UPDATE {SCHEMA}.pricing_accounts a
            SET segment_id = r.id
            FROM {SCHEMA}.segments r
            WHERE r.parent_id IS NULL
              AND r.slug = a.department::text
              AND a.segment_id IS NULL
            """
        )
    )

    # ---------------------------------------------------------- product backfill
    # Each product → leaf segment whose parent.slug = department AND
    # sort_order = product_type - 1.
    bind.execute(
        sa.text(
            f"""
            UPDATE {SCHEMA}.pricing_products p
            SET segment_id = s.id
            FROM {SCHEMA}.segments s
            JOIN {SCHEMA}.segments r ON r.id = s.parent_id
            WHERE r.parent_id IS NULL
              AND r.slug = p.department::text
              AND s.sort_order = (COALESCE(p.product_type, 1) - 1)
              AND p.segment_id IS NULL
            """
        )
    )


def downgrade() -> None:
    op.drop_index(
        "ix_pricing_products_segment_id",
        table_name="pricing_products",
        schema=SCHEMA,
    )
    op.drop_column("pricing_products", "segment_id", schema=SCHEMA)
    op.drop_index(
        "ix_pricing_accounts_segment_id",
        table_name="pricing_accounts",
        schema=SCHEMA,
    )
    op.drop_column("pricing_accounts", "segment_id", schema=SCHEMA)
