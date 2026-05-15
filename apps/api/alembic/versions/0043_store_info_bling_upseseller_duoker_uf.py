# ruff: noqa: E501
"""store_info: bling_store_id + upseseller + duoker + uf_restrictions

Lojas screen gains four editable fields per (platform, account) row:
- bling_store_id (text): per-store Bling identifier
- upseseller (bool): account is connected to UpseSeller
- duoker (bool): account is connected to Duoker
- uf_restrictions (text[]): list of Brazilian UF codes this account ships
  to. NULL = no restriction.

Revision ID: 0043_store_info_extra_cols
Revises: 0042_vw_pricing_join
Create Date: 2026-05-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0043_store_info_extra_cols"
down_revision: str | None = "0042_vw_pricing_join"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "store_info",
        sa.Column("bling_store_id", sa.Text(), nullable=True),
        schema="davinci",
    )
    op.add_column(
        "store_info",
        sa.Column("upseseller", sa.Boolean(), nullable=True),
        schema="davinci",
    )
    op.add_column(
        "store_info",
        sa.Column("duoker", sa.Boolean(), nullable=True),
        schema="davinci",
    )
    op.add_column(
        "store_info",
        sa.Column(
            "uf_restrictions",
            postgresql.ARRAY(sa.Text()),
            nullable=True,
        ),
        schema="davinci",
    )


def downgrade() -> None:
    op.drop_column("store_info", "uf_restrictions", schema="davinci")
    op.drop_column("store_info", "duoker", schema="davinci")
    op.drop_column("store_info", "upseseller", schema="davinci")
    op.drop_column("store_info", "bling_store_id", schema="davinci")
