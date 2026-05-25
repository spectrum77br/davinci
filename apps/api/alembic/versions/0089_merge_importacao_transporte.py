# ruff: noqa: E501
"""Merge importacao_tables and bling_orders_transporte heads.

Both 0088 migrations branched from 0087_pricing_overrides_cell_color in
parallel. Empty merge — only joins the chains.

Revision ID: 0089_merge_importacao_transporte
Revises: 0088_importacao_tables, 0088_bling_orders_transporte
Create Date: 2026-05-25
"""

from collections.abc import Sequence

revision: str = "0089_merge_importacao_transporte"
down_revision: tuple[str, str] | None = (
    "0088_importacao_tables",
    "0088_bling_orders_transporte",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
