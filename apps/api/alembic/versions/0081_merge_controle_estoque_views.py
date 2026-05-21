"""merge: controle_estoque chain + views chain (post-0080)

Revision ID: 0081_merge_controle_estoque_views
Revises: 0079_merge_controle_estoque_devolutions, 0080_vw_conciliacao_margens_all
Create Date: 2026-05-21

Two parallel migration heads landed during this round of work:
  * 0079_merge_controle_estoque_devolutions — adds controle-estoque +
    earlier devolutions merge.
  * 0080_vw_conciliacao_margens_all — view tweak pushed independently.

Pure merge, no schema change. Lets `alembic upgrade head` resolve.
"""

from collections.abc import Sequence

revision: str = "0081_merge_controle_estoque_views"
down_revision: tuple[str, ...] = (
    "0079_merge_controle_estoque_devolutions",
    "0080_vw_conciliacao_margens_all",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
