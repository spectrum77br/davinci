"""Durable intentions and asynchronous Mirakl stock/price imports.

Credentials never enter these rows. A committed ``submitting`` batch is a
write-ahead fence: after an interrupted POST it must not be sent again.
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class CarrefourOfferWrite(Base, TimestampMixin):
    __tablename__ = "carrefour_offer_writes"
    __table_args__ = (
        Index("uq_carrefour_offer_write_target", "integration_id", "offer_id", unique=True),
        Index("ix_carrefour_offer_write_queue", "integration_id", "status", "updated_at"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    integration_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("integrations.id", ondelete="CASCADE"),
        nullable=False,
    )
    link_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("product_links.id", ondelete="CASCADE"),
        nullable=False,
    )
    offer_id: Mapped[str] = mapped_column(String(32), nullable=False)
    sku: Mapped[str] = mapped_column(Text, nullable=False)
    requested_stock: Mapped[int | None] = mapped_column(Integer)
    requested_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    confirmed_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    last_error: Mapped[str | None] = mapped_column(Text)


class CarrefourOfferBatch(Base, TimestampMixin):
    __tablename__ = "carrefour_offer_batches"
    __table_args__ = (
        Index("ix_carrefour_offer_batch_latest", "integration_id", "created_at"),
        Index(
            "uq_carrefour_offer_batch_active",
            "integration_id",
            unique=True,
            postgresql_where=text("state IN ('preparing','submitting','submitted','uncertain')"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    integration_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("integrations.id", ondelete="CASCADE"),
        nullable=False,
    )
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="preparing")
    kind: Mapped[str] = mapped_column(String(12), nullable=False)
    shop_id: Mapped[str] = mapped_column(String(32), nullable=False)
    import_id: Mapped[str | None] = mapped_column(String(128))
    # request_id, revision, target offer/SKU, explicit stock/price and the
    # mapped offer sent. No raw HTTP response or credentials are retained.
    items: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
