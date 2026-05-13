"""segments taxonomy table + product.segment_id FK + seed roots

Revision ID: 0023_segments
Revises: 0022_user_duoke
Create Date: 2026-05-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0023_segments"
down_revision: str | None = "0022_user_duoke"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"

ROOTS = (
    ("celular", "Celular"),
    ("mala", "Mala"),
    ("eletro", "Eletro"),
    ("catalogo", "Catálogo"),
)


def upgrade() -> None:
    op.create_table(
        "segments",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "parent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.segments.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("slug", sa.String(128), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("parent_id", "slug", name="uq_segments_parent_slug"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_segments_parent_id",
        "segments",
        ["parent_id"],
        schema=SCHEMA,
    )

    op.add_column(
        "products",
        sa.Column(
            "segment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.segments.id", ondelete="SET NULL"),
            nullable=True,
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_products_segment_id",
        "products",
        ["segment_id"],
        schema=SCHEMA,
    )

    # Seed roots mirroring the existing Department enum so downstream Phase 2
    # can pivot from `pricing_*.department` to `pricing_*.segment_id` without
    # ambiguity. Seeded with user_id=NULL (global taxonomy).
    bind = op.get_bind()
    for idx, (slug, name) in enumerate(ROOTS):
        bind.execute(
            sa.text(
                f"INSERT INTO {SCHEMA}.segments (user_id, parent_id, name, slug, sort_order) "
                "VALUES (NULL, NULL, :name, :slug, :ord)"
            ),
            {"name": name, "slug": slug, "ord": idx},
        )


def downgrade() -> None:
    op.drop_index("ix_products_segment_id", table_name="products", schema=SCHEMA)
    op.drop_column("products", "segment_id", schema=SCHEMA)
    op.drop_index("ix_segments_parent_id", table_name="segments", schema=SCHEMA)
    op.drop_table("segments", schema=SCHEMA)
