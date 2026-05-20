"""merge heads: marketing (credit_balance_at) + refunds runtime defaults

Revision ID: 0070_merge_marketing_refunds_heads
Revises: 0069_marketing_account_credit_balance_at, 0069_refunds_runtime_defaults
Create Date: 2026-05-20

Both 0069_* migrations chain off 0068_integration_consecutive_errors,
so alembic sees two heads and refuses `upgrade head`. This is a pure
merge — no schema change, just unifies the graph.
"""

from collections.abc import Sequence

revision: str = "0070_merge_marketing_refunds_heads"
down_revision: tuple[str, ...] = (
    "0069_marketing_account_credit_balance_at",
    "0069_refunds_runtime_defaults",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
