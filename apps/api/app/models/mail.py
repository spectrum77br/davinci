"""Private mail central. Tuta credentials stay on the Mac; contents are encrypted.

These records deliberately do not enter the public Atendimento conversation
tables: their current staff-wide read permissions do not apply to private mail.
"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class MailMailbox(Base, TimestampMixin):
    __tablename__ = "mail_mailboxes"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    config_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    agent_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    send_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    agent_can_send: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="offline")
    error_code: Mapped[str | None] = mapped_column(String(64))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MailMessage(Base, TimestampMixin):
    __tablename__ = "mail_messages"
    __table_args__ = (
        UniqueConstraint("mailbox_id", "source_id"),
        Index("ix_mail_messages_mailbox_received", "mailbox_id", "received_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    mailbox_id: Mapped[UUID] = mapped_column(
        ForeignKey("mail_mailboxes.id", ondelete="CASCADE"), nullable=False
    )
    source_id: Mapped[str] = mapped_column(String(191), nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    content_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    attachment_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class MailAttachment(Base):
    __tablename__ = "mail_attachments"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    message_id: Mapped[UUID] = mapped_column(
        ForeignKey("mail_messages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    metadata_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    content_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)


class MailOutbox(Base, TimestampMixin):
    __tablename__ = "mail_outbox"
    __table_args__ = (
        UniqueConstraint("mailbox_id", "request_id"),
        Index("ix_mail_outbox_mailbox_status", "mailbox_id", "status", "created_at"),
        Index(
            "uq_mail_outbox_active_reply",
            "message_id",
            unique=True,
            postgresql_where=text("status IN ('queued', 'leased', 'uncertain')"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    mailbox_id: Mapped[UUID] = mapped_column(
        ForeignKey("mail_mailboxes.id", ondelete="CASCADE"), nullable=False
    )
    message_id: Mapped[UUID] = mapped_column(
        ForeignKey("mail_messages.id", ondelete="CASCADE"), nullable=False
    )
    author_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    request_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    content_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    lease_token_hash: Mapped[str | None] = mapped_column(String(64))
    leased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    receipt_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    error_code: Mapped[str | None] = mapped_column(String(64))
