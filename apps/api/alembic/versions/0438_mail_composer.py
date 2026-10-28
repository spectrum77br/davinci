"""Human mail composition and private encrypted drafts."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.config import get_settings

revision = "0438_mail_composer"
down_revision = "0437_conciliacao_recomeca_24_10"
branch_labels = None
depends_on = None
SCHEMA = get_settings().database_schema


def upgrade():
    op.alter_column("mail_outbox", "message_id", nullable=True, schema=SCHEMA)
    op.create_table(
        "mail_drafts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mailbox_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("author_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content_enc", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["mailbox_id"], [f"{SCHEMA}.mail_mailboxes.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["author_user_id"], [f"{SCHEMA}.users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_mail_drafts_author_mailbox",
        "mail_drafts",
        ["author_user_id", "mailbox_id"],
        schema=SCHEMA,
    )


def downgrade():
    # New-message audit rows must be retained. Refuse downgrade while they
    # exist rather than deleting somebody's sent/queued email history.
    outbox = sa.table("mail_outbox", sa.column("message_id"), schema=SCHEMA)
    if op.get_bind().scalar(sa.select(sa.literal(1)).where(outbox.c.message_id.is_(None)).limit(1)):
        raise RuntimeError("Cannot downgrade while composed new-message history exists")
    op.drop_table("mail_drafts", schema=SCHEMA)
    op.alter_column("mail_outbox", "message_id", nullable=False, schema=SCHEMA)
