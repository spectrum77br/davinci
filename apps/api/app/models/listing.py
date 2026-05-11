from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin
from app.models.enums import (
    IntegrationPlatform,
    ListingRequestStatus,
    ListingStatus,
)


def _enum(py_enum, name: str):
    return Enum(
        py_enum,
        name=name,
        schema=None,
        create_type=False,
        values_callable=lambda x: [e.value for e in x],
    )


class Listing(Base, TimestampMixin):
    __tablename__ = "listings"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    integration_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("integrations.id", ondelete="CASCADE"),
        nullable=False,
    )
    platform: Mapped[IntegrationPlatform] = mapped_column(
        _enum(IntegrationPlatform, "integration_platform"),
        nullable=False,
    )
    external_id: Mapped[str] = mapped_column(Text, nullable=False)
    sku: Mapped[str | None] = mapped_column(Text, nullable=True)
    listing_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    price: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    stock: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[ListingStatus] = mapped_column(
        _enum(ListingStatus, "listing_status"),
        nullable=False,
        default=ListingStatus.ACTIVE,
        server_default=text("'active'"),
    )
    category: Mapped[str | None] = mapped_column(Text, nullable=True)
    thumbnail_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    product_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="SET NULL"),
        nullable=True,
    )
    raw_data: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class ListingRequest(Base, TimestampMixin):
    __tablename__ = "listing_requests"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    platform: Mapped[IntegrationPlatform] = mapped_column(
        _enum(IntegrationPlatform, "integration_platform"),
        nullable=False,
    )
    sku: Mapped[str | None] = mapped_column(Text, nullable=True)
    product_name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_price: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    category: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[ListingRequestStatus] = mapped_column(
        _enum(ListingRequestStatus, "listing_request_status"),
        nullable=False,
        default=ListingRequestStatus.PENDING,
        server_default=text("'pending'"),
    )
