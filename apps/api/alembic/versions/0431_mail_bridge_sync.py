"""Stable identities for automatic Tuta folder synchronization."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.config import get_settings

revision = "0431_mail_bridge_sync"
down_revision = "0430_integration_carrefour"
branch_labels = None
depends_on = None
SCHEMA = get_settings().database_schema


def upgrade():
    op.create_table(
        "mail_bridge_identities",
        sa.Column("mailbox_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("canonical_message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("folder_id", sa.String(191), nullable=True),
        sa.Column(
            "message_ids", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("missing_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["mailbox_id"], [f"{SCHEMA}.mail_mailboxes.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["canonical_message_id"], [f"{SCHEMA}.mail_messages.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("mailbox_id", "digest"),
        schema=SCHEMA,
    )


def downgrade():
    op.drop_table("mail_bridge_identities", schema=SCHEMA)
