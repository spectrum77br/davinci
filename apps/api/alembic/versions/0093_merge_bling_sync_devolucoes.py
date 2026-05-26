# ruff: noqa: E501
"""Merge: 0091_import_products_bling_sync + 0092_vw_devolucoes_slim.

Two branches diverged at 0090_import_products_tsa_int:
  * 0091_import_products_bling_sync  (this branch — importacao feature)
  * 0091_vw_devolucoes → 0092_vw_devolucoes_slim  (devolucoes branch)

This is a structural merge — no DDL, just unifies the two heads.

Revision ID: 0093_merge_bling_sync_devolucoes
Revises: 0091_import_products_bling_sync, 0092_vw_devolucoes_slim
Create Date: 2026-05-26
"""

from collections.abc import Sequence

revision: str = "0093_merge_bling_sync_devolucoes"
down_revision: tuple[str, ...] = (
    "0091_import_products_bling_sync",
    "0092_vw_devolucoes_slim",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
