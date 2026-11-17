"""Durable, per-integration Carrefour offer write queue; all scopes stay off.

Revision ID: 0498_carrefour_offer_write
Revises: 0497_sistema_mudanca_pergunta
"""

# Fixed internal schema only; no user-provided SQL identifiers.
# ruff: noqa: S608

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0498_carrefour_offer_write"
down_revision = "0497_sistema_mudanca_pergunta"
branch_labels = None
depends_on = None
SCHEMA = "davinci"


def upgrade() -> None:
    op.create_table(
        "carrefour_offer_writes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "integration_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.integrations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "link_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.product_links.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("offer_id", sa.String(32), nullable=False),
        sa.Column("sku", sa.Text(), nullable=False),
        sa.Column("requested_stock", sa.Integer()),
        sa.Column("requested_price", sa.Numeric(14, 2)),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("confirmed_revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("last_error", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_carrefour_offer_write_target",
        "carrefour_offer_writes",
        ["integration_id", "offer_id"],
        unique=True,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_carrefour_offer_write_queue",
        "carrefour_offer_writes",
        ["integration_id", "status", "updated_at"],
        schema=SCHEMA,
    )
    op.create_table(
        "carrefour_offer_batches",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "integration_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.integrations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("shop_id", sa.String(32), nullable=False),
        sa.Column("import_id", sa.String(128)),
        sa.Column("items", postgresql.JSONB(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("next_check_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_carrefour_offer_batch_latest",
        "carrefour_offer_batches",
        ["integration_id", "created_at"],
        schema=SCHEMA,
    )
    op.create_index(
        "uq_carrefour_offer_batch_active",
        "carrefour_offer_batches",
        ["integration_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("state IN ('preparing','submitting','submitted','uncertain')"),
    )


def downgrade() -> None:
    # A downgrade must not discard unresolved remote submissions. Stop here
    # until the operator has reconciled them and removed the feature explicitly.
    op.execute(f"""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM {SCHEMA}.carrefour_offer_batches
                     WHERE state IN ('preparing','submitting','submitted','uncertain')) THEN
            RAISE EXCEPTION 'Reconcile Carrefour imports before downgrade';
          END IF;
        END $$;
    """)
    op.drop_table("carrefour_offer_batches", schema=SCHEMA)
    op.drop_table("carrefour_offer_writes", schema=SCHEMA)
