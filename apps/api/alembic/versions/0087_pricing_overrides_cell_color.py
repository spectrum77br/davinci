# ruff: noqa: E501
"""pricing_overrides: cell_color — Excel-style per-cell highlight color.

Adds a nullable VARCHAR(20) column. Operator picks one of 8 colors
(red/orange/yellow/green/blue/purple/pink/gray) from a swatch in the
toolbar of /pricing/tabela; the value persists on the same row as the
price/status override. NULL = no highlight (default; cellTone falls
back to the existing automatic palette).

The set of allowed values is whitelisted at the API layer rather than
in a CHECK constraint so we can add/rename colors without a migration.

Revision ID: 0087_pricing_overrides_cell_color
Revises: 0086_dnp_descricao_foto
Create Date: 2026-05-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0087_pricing_overrides_cell_color"
down_revision: str | None = "0086_dnp_descricao_foto"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column(
        "pricing_overrides",
        sa.Column("cell_color", sa.String(20), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("pricing_overrides", "cell_color", schema=SCHEMA)
