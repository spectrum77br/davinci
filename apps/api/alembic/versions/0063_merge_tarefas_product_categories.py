"""merge tarefas and product category heads

Revision ID: 0063_merge_tarefas_product_categories
Revises: 0061_tarefas, 0062_backfill_bling_order_category_names
Create Date: 2026-05-18
"""

from collections.abc import Sequence

revision: str = "0063_merge_tarefas_product_categories"
down_revision: tuple[str, str] | None = (
    "0061_tarefas",
    "0062_backfill_bling_order_category_names",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
