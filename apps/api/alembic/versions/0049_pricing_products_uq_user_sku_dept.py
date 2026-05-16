# ruff: noqa: E501
"""widen pricing_products unique constraint to (user_id, sku, department)

The original UQ `uq_pricing_products_user_sku` rejected legitimate rows when
the same SKU needs separate pricing per department (e.g. an item that sells
as both "celular" and "catalogo" with different cost_kit / margin slots).

Production was already changed directly to
`uq_pricing_products_user_sku_dept`; this migration documents the change so
fresh environments reproduce it and the ORM model matches.

Revision ID: 0049_pricing_products_uq_user_sku_dept
Revises: 0048_pricing_accounts_slot_segments
Create Date: 2026-05-16
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0049_pricing_products_uq_user_sku_dept"
down_revision: str | None = "0048_pricing_accounts_slot_segments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.drop_constraint(
        "uq_pricing_products_user_sku",
        "pricing_products",
        schema=SCHEMA,
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_pricing_products_user_sku_dept",
        "pricing_products",
        ["user_id", "sku", "department"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_pricing_products_user_sku_dept",
        "pricing_products",
        schema=SCHEMA,
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_pricing_products_user_sku",
        "pricing_products",
        ["user_id", "sku"],
        schema=SCHEMA,
    )
