"""drop pricing_*.department + pricing_products.product_type + Department enum (Phase 2c)

`products.segment_id` stays nullable because some products are not yet
classified. `pricing_*.segment_id` becomes NOT NULL (backfill in 0026 covered
100% of rows).

Revision ID: 0027_drop_pricing_department
Revises: 0026_pricing_segment_fks
Create Date: 2026-05-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027_drop_pricing_department"
down_revision: str | None = "0026_pricing_segment_fks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    # Guard: bail out if any row still lacks segment_id — would silently lose
    # the classification when we drop department/product_type.
    bind = op.get_bind()
    bad_acc = bind.execute(
        sa.text(
            f"SELECT COUNT(*) FROM {SCHEMA}.pricing_accounts WHERE segment_id IS NULL"
        )
    ).scalar_one()
    bad_prod = bind.execute(
        sa.text(
            f"SELECT COUNT(*) FROM {SCHEMA}.pricing_products WHERE segment_id IS NULL"
        )
    ).scalar_one()
    if bad_acc or bad_prod:
        raise RuntimeError(
            f"refusing to drop department: {bad_acc} pricing_accounts and "
            f"{bad_prod} pricing_products still have NULL segment_id"
        )

    op.alter_column(
        "pricing_accounts", "segment_id", nullable=False, schema=SCHEMA
    )
    op.alter_column(
        "pricing_products", "segment_id", nullable=False, schema=SCHEMA
    )

    op.drop_column("pricing_accounts", "department", schema=SCHEMA)
    op.drop_column("pricing_products", "department", schema=SCHEMA)
    op.drop_column("pricing_products", "product_type", schema=SCHEMA)

    # Enum type is now orphaned.
    sa.Enum(name="department", schema=SCHEMA).drop(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    DEPARTMENTS = ("celular", "mala", "eletro", "catalogo")
    sa.Enum(*DEPARTMENTS, name="department", schema=SCHEMA).create(
        bind, checkfirst=True
    )

    op.add_column(
        "pricing_accounts",
        sa.Column(
            "department",
            sa.Enum(*DEPARTMENTS, name="department", schema=SCHEMA, create_type=False),
            nullable=True,
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "pricing_products",
        sa.Column(
            "department",
            sa.Enum(*DEPARTMENTS, name="department", schema=SCHEMA, create_type=False),
            nullable=True,
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "pricing_products",
        sa.Column("product_type", sa.Integer(), nullable=True),
        schema=SCHEMA,
    )

    # Repopulate from segment_id.
    bind.execute(
        sa.text(
            f"""
            UPDATE {SCHEMA}.pricing_accounts a
            SET department = r.slug::{SCHEMA}.department
            FROM {SCHEMA}.segments r
            WHERE r.id = a.segment_id AND r.parent_id IS NULL
            """
        )
    )
    bind.execute(
        sa.text(
            f"""
            UPDATE {SCHEMA}.pricing_products p
            SET department = r.slug::{SCHEMA}.department,
                product_type = s.sort_order + 1
            FROM {SCHEMA}.segments s
            JOIN {SCHEMA}.segments r ON r.id = s.parent_id
            WHERE s.id = p.segment_id AND r.parent_id IS NULL
            """
        )
    )

    op.alter_column(
        "pricing_accounts", "department", nullable=False, schema=SCHEMA
    )
    op.alter_column(
        "pricing_products", "department", nullable=False, schema=SCHEMA
    )
    op.alter_column(
        "pricing_products", "product_type", nullable=False, schema=SCHEMA
    )
    op.alter_column(
        "pricing_accounts", "segment_id", nullable=True, schema=SCHEMA
    )
    op.alter_column(
        "pricing_products", "segment_id", nullable=True, schema=SCHEMA
    )
