"""merge: controle_estoque + devolutions

Revision ID: 0079_merge_controle_estoque_devolutions
Revises: 0078_controle_estoque, 0078_devolutions
Create Date: 2026-05-21

Both 0078_* chain off 0077; alembic refuses upgrade head until merged.
Pure merge, no schema change.
"""

from collections.abc import Sequence

revision: str = "0079_merge_controle_estoque_devolutions"
down_revision: tuple[str, ...] = (
    "0078_controle_estoque",
    "0078_devolutions",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
