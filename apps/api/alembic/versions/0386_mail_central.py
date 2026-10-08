"""Private mailboxes, encrypted messages/attachments and a human reply outbox.

No mailbox credentials, seed accounts, automatic replies or production data changes.
Revision ID: 0386_mail_central
Revises: 0385_flex_origem
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "0386_mail_central"
down_revision = "0385_flex_origem"
branch_labels = None
depends_on = None
SCHEMA = "davinci"


def _timestamps():
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    ]


def upgrade():
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.create_table(
        "mail_mailboxes",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "owner_user_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.users.id"),
            nullable=False,
        ),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("config_enc", sa.LargeBinary(), nullable=False),
        sa.Column("agent_token_hash", sa.String(64), nullable=False),
        sa.Column("send_enabled", sa.Boolean(), nullable=False),
        sa.Column("agent_can_send", sa.Boolean(), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("last_sync_at", sa.DateTime(timezone=True)),
        *_timestamps(),
        schema=SCHEMA,
    )
    op.create_table(
        "mail_messages",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "mailbox_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.mail_mailboxes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_id", sa.String(191), nullable=False),
        sa.Column("direction", sa.String(16), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_enc", sa.LargeBinary(), nullable=False),
        sa.Column("attachment_count", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "mailbox_id", "source_id", name="uq_mail_messages_mailbox_id_source_id"
        ),
        *_timestamps(),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_mail_messages_mailbox_received",
        "mail_messages",
        ["mailbox_id", "received_at", "id"],
        schema=SCHEMA,
    )
    op.create_table(
        "mail_attachments",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "message_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.mail_messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("metadata_enc", sa.LargeBinary(), nullable=False),
        sa.Column("content_enc", sa.LargeBinary(), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_mail_attachments_message_id", "mail_attachments", ["message_id"], schema=SCHEMA
    )
    op.create_table(
        "mail_outbox",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "mailbox_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.mail_mailboxes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "message_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.mail_messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "author_user_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.users.id"),
            nullable=False,
        ),
        sa.Column("request_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("content_enc", sa.LargeBinary(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("lease_token_hash", sa.String(64)),
        sa.Column("leased_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("receipt_enc", sa.LargeBinary()),
        sa.Column("error_code", sa.String(64)),
        sa.UniqueConstraint(
            "mailbox_id", "request_id", name="uq_mail_outbox_mailbox_id_request_id"
        ),
        *_timestamps(),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_mail_outbox_mailbox_status",
        "mail_outbox",
        ["mailbox_id", "status", "created_at"],
        schema=SCHEMA,
    )
    op.create_index(
        "uq_mail_outbox_active_reply",
        "mail_outbox",
        ["message_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'leased', 'uncertain')"),
        schema=SCHEMA,
    )


def downgrade():
    for name in ("mail_outbox", "mail_attachments", "mail_messages", "mail_mailboxes"):
        op.drop_table(name, schema=SCHEMA)
