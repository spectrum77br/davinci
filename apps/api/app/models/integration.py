from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, LargeBinary, String, Text, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin
from app.models.enums import IntegrationPlatform


def _enum(py_enum, name: str):
    return Enum(
        py_enum,
        name=name,
        schema=None,
        create_type=False,
        values_callable=lambda x: [e.value for e in x],
    )


class Integration(Base, TimestampMixin):
    __tablename__ = "integrations"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    store_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("stores.id", ondelete="SET NULL"),
        unique=True,
        nullable=True,
    )
    platform: Mapped[IntegrationPlatform] = mapped_column(
        _enum(IntegrationPlatform, "integration_platform"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    credentials: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'active'"))
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class OAuthState(Base):
    __tablename__ = "oauth_states"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    state: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    platform: Mapped[IntegrationPlatform] = mapped_column(
        _enum(IntegrationPlatform, "integration_platform"), nullable=False
    )
    store_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("stores.id", ondelete="CASCADE"),
        nullable=True,
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    code_verifier: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
