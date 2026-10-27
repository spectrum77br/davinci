"""Mailbox-scoped hashed mail identity and opaque folder IDs; names stay encrypted."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class MailBridgeIdentity(Base):
    __tablename__ = "mail_bridge_identities"

    mailbox_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("mail_mailboxes.id", ondelete="CASCADE"),
        primary_key=True,
    )
    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    canonical_message_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("mail_messages.id", ondelete="CASCADE"),
        nullable=False,
    )
    folder_id: Mapped[str | None] = mapped_column(String(191))
    message_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # Presence/placement transition checkpoint; latest complete observation is
    # the encrypted mailbox catalog observed_at, even when no message moved.
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    missing_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
