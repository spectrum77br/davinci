"""listings + listing_requests (Fase 8)

Revision ID: 0010_listings
Revises: 0009_user_settings_phase7
Create Date: 2026-05-06
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010_listings"
down_revision: Union[str, None] = "0009_user_settings_phase7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "davinci"

LISTING_STATUSES = ("active", "paused", "closed", "under_review", "inactive")
LISTING_REQUEST_STATUSES = ("pending", "in_progress", "completed", "rejected")


def upgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')

    bind = op.get_bind()
    sa.Enum(*LISTING_STATUSES, name="listing_status", schema=SCHEMA).create(bind, checkfirst=True)
    sa.Enum(
        *LISTING_REQUEST_STATUSES, name="listing_request_status", schema=SCHEMA
    ).create(bind, checkfirst=True)

    op.create_table(
        "listings",
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
            nullable=False,
        ),
        sa.Column(
            "integration_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.integrations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "platform",
            postgresql.ENUM(
                "bling", "ml", "shopee", "amazon",
                name="integration_platform",
                schema=SCHEMA,
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("sku", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("price", sa.BigInteger(), nullable=True, comment="cents"),
        sa.Column("stock", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(
                *LISTING_STATUSES,
                name="listing_status",
                schema=SCHEMA,
                create_type=False,
            ),
            nullable=False,
            server_default=sa.text("'active'"),
        ),
        sa.Column("category", sa.Text(), nullable=True),
        sa.Column("thumbnail_url", sa.Text(), nullable=True),
        sa.Column(
            "product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.products.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "raw_data",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "imported_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "integration_id", "external_id", name="uq_listings_integration_external"
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_listings_user_id", "listings", ["user_id"], schema=SCHEMA)
    op.create_index(
        "ix_listings_user_sku", "listings", ["user_id", "sku"], schema=SCHEMA
    )
    op.create_index(
        "ix_listings_product_id", "listings", ["product_id"], schema=SCHEMA
    )
    op.create_index("ix_listings_platform", "listings", ["platform"], schema=SCHEMA)
    # Partial index to drive the auto_import_link cron query cheaply.
    op.execute(
        f'CREATE INDEX ix_listings_unlinked '
        f'ON "{SCHEMA}".listings (user_id, sku) '
        f'WHERE product_id IS NULL AND sku IS NOT NULL'
    )

    op.execute(
        f'CREATE TRIGGER trg_listings_updated_at '
        f'BEFORE UPDATE ON "{SCHEMA}".listings '
        f'FOR EACH ROW EXECUTE FUNCTION "{SCHEMA}".set_updated_at()'
    )

    op.create_table(
        "listing_requests",
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
            nullable=False,
        ),
        sa.Column(
            "platform",
            postgresql.ENUM(
                "bling", "ml", "shopee", "amazon",
                name="integration_platform",
                schema=SCHEMA,
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("sku", sa.Text(), nullable=True),
        sa.Column("product_name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("requested_price", sa.BigInteger(), nullable=True, comment="cents"),
        sa.Column("category", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(
                *LISTING_REQUEST_STATUSES,
                name="listing_request_status",
                schema=SCHEMA,
                create_type=False,
            ),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_listing_requests_user_id", "listing_requests", ["user_id"], schema=SCHEMA
    )
    op.create_index(
        "ix_listing_requests_status", "listing_requests", ["status"], schema=SCHEMA
    )

    op.execute(
        f'CREATE TRIGGER trg_listing_requests_updated_at '
        f'BEFORE UPDATE ON "{SCHEMA}".listing_requests '
        f'FOR EACH ROW EXECUTE FUNCTION "{SCHEMA}".set_updated_at()'
    )


def downgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.execute(
        f'DROP TRIGGER IF EXISTS trg_listing_requests_updated_at '
        f'ON "{SCHEMA}".listing_requests'
    )
    op.drop_index("ix_listing_requests_status", table_name="listing_requests", schema=SCHEMA)
    op.drop_index("ix_listing_requests_user_id", table_name="listing_requests", schema=SCHEMA)
    op.drop_table("listing_requests", schema=SCHEMA)

    op.execute(f'DROP TRIGGER IF EXISTS trg_listings_updated_at ON "{SCHEMA}".listings')
    op.execute(f'DROP INDEX IF EXISTS "{SCHEMA}".ix_listings_unlinked')
    op.drop_index("ix_listings_platform", table_name="listings", schema=SCHEMA)
    op.drop_index("ix_listings_product_id", table_name="listings", schema=SCHEMA)
    op.drop_index("ix_listings_user_sku", table_name="listings", schema=SCHEMA)
    op.drop_index("ix_listings_user_id", table_name="listings", schema=SCHEMA)
    op.drop_table("listings", schema=SCHEMA)

    bind = op.get_bind()
    sa.Enum(name="listing_request_status", schema=SCHEMA).drop(bind, checkfirst=True)
    sa.Enum(name="listing_status", schema=SCHEMA).drop(bind, checkfirst=True)
