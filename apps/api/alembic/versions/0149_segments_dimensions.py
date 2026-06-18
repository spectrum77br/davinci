"""add segments dimensions: altura, largura, comprimento, peso

Per-subsegment physical defaults used to pre-fill product dimensions.
Dimensions (altura/largura/comprimento) are stored in centimetres and
`peso` in kilograms, all Numeric(10,3). Nullable: roots and any subsegment
default to NULL until the user fills it in (mirrors `min_margin`).

Revision ID: 0149_segments_dimensions
Revises: 0148_conciliacao_margens_for_bling_id_fn
Create Date: 2026-06-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0149_segments_dimensions"
down_revision: str | None = "0148_conciliacao_margens_for_bling_id_fn"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
COLUMNS = ("altura", "largura", "comprimento", "peso")


def upgrade() -> None:
    for col in COLUMNS:
        op.add_column(
            "segments",
            sa.Column(col, sa.Numeric(10, 3), nullable=True),
            schema=SCHEMA,
        )


def downgrade() -> None:
    for col in reversed(COLUMNS):
        op.drop_column("segments", col, schema=SCHEMA)
