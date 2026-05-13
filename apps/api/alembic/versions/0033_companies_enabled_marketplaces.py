"""add `enabled_marketplaces` (text[]) to companies

Whitelists the marketplaces a company is allowed to open Stores in. The
`/companies/grid` UI uses it to gate the per-marketplace "+" button (red ×
when blocked, "+" when enabled). Default backfill = ALL canonical
marketplaces so existing rows behave as before the gate landed.

Revision ID: 0033_companies_enabled_marketplaces
Revises: 0032_vw_bling_pedidos_verificado
Create Date: 2026-05-13
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0033_companies_enabled_marketplaces"
down_revision: str | None = "0032_vw_bling_pedidos_verificado"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
ALL_MK = (
    "ml", "shopee", "amazon", "aliexpress",
    "temu", "tiktok", "shein", "magalu", "site",
)


def upgrade() -> None:
    op.add_column(
        "companies",
        sa.Column(
            "enabled_marketplaces",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text(
                "ARRAY[" + ",".join(f"'{m}'" for m in ALL_MK) + "]::text[]"
            ),
        ),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("companies", "enabled_marketplaces", schema=SCHEMA)
