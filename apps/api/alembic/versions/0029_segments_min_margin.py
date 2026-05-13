"""add segments.min_margin + seed per subtype (canonical reference)

Stored as a fraction (0.1500 = 15%), Numeric(6,4) — mirrors
`pricing_accounts.margin*`. Used to flag products whose effective margin
falls below the floor configured for their subtype. Nullable: roots and any
custom subtypes default to NULL until the user fills it in.

Seed values come from the canonical "Margens" spreadsheet:

  Celular: acessorios 15%, diversos 8%, regular 14%, robusto 14%, apple 7%
  Mala:    acessorios 15%, 12" 15%, 18-20 15%, 24-acima 15%, queima -15%
  Eletro:  1..5 = 0%

Revision ID: 0029_segments_min_margin
Revises: 0028_readd_department
Create Date: 2026-05-13
"""

from collections.abc import Sequence
from decimal import Decimal

import sqlalchemy as sa
from alembic import op

revision: str = "0029_segments_min_margin"
down_revision: str | None = "0028_readd_department"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"

# (root_slug, child_slug, min_margin)
SEED: list[tuple[str, str, Decimal]] = [
    ("celular", "acessorios", Decimal("0.15")),
    ("celular", "diversos", Decimal("0.08")),
    ("celular", "regular", Decimal("0.14")),
    ("celular", "robusto", Decimal("0.14")),
    ("celular", "apple", Decimal("0.07")),
    ("mala", "acessorios", Decimal("0.15")),
    ("mala", "12", Decimal("0.15")),
    ("mala", "18-20", Decimal("0.15")),
    ("mala", "24-acima", Decimal("0.15")),
    ("mala", "queima-estoque", Decimal("-0.15")),
    ("eletro", "1", Decimal("0")),
    ("eletro", "2", Decimal("0")),
    ("eletro", "3", Decimal("0")),
    ("eletro", "4", Decimal("0")),
    ("eletro", "5", Decimal("0")),
]


def upgrade() -> None:
    op.add_column(
        "segments",
        sa.Column("min_margin", sa.Numeric(6, 4), nullable=True),
        schema=SCHEMA,
    )
    bind = op.get_bind()
    for root_slug, child_slug, margin in SEED:
        bind.execute(
            sa.text(
                f"""
                UPDATE {SCHEMA}.segments s
                SET min_margin = :margin
                FROM {SCHEMA}.segments r
                WHERE r.parent_id IS NULL
                  AND r.slug = :root_slug
                  AND s.parent_id = r.id
                  AND s.slug = :child_slug
                """
            ),
            {"margin": margin, "root_slug": root_slug, "child_slug": child_slug},
        )


def downgrade() -> None:
    op.drop_column("segments", "min_margin", schema=SCHEMA)
