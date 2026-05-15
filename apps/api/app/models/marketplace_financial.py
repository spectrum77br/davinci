from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
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


class MarketplaceOrderFinancial(Base, TimestampMixin):
    __tablename__ = "marketplace_order_financials"
    __table_args__ = (
        UniqueConstraint(
            "platform",
            "integration_id",
            "external_order_id",
            name="uq_marketplace_order_financials_platform_integration_order",
        ),
        Index("ix_marketplace_order_financials_bling_id", "bling_id"),
        Index("ix_marketplace_order_financials_store_fetched", "store_id", "fetched_at"),
        Index(
            "ix_marketplace_order_financials_retry",
            "status",
            "next_retry_at",
            postgresql_where=text("next_retry_at IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    platform: Mapped[IntegrationPlatform] = mapped_column(
        _enum(IntegrationPlatform, "integration_platform"), nullable=False
    )
    integration_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("integrations.id", ondelete="SET NULL"),
        nullable=True,
    )
    store_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("stores.id", ondelete="SET NULL"),
        nullable=True,
    )
    bling_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    pedido_bling: Mapped[str | None] = mapped_column(Text, nullable=True)
    external_order_id: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'pending'")
    )
    currency: Mapped[str] = mapped_column(String(8), nullable=False, server_default=text("'BRL'"))
    gross_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    fee_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    freight_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    rebate_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    discount_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    refund_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    tax_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    adjustment_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    net_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    raw: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"), default=dict
    )
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class MarketplaceFinancialEvent(Base, TimestampMixin):
    __tablename__ = "marketplace_financial_events"
    __table_args__ = (
        Index("ix_marketplace_financial_events_order", "order_financial_id"),
        Index("ix_marketplace_financial_events_bling_type", "bling_id", "event_type"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    order_financial_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("marketplace_order_financials.id", ondelete="CASCADE"),
        nullable=False,
    )
    platform: Mapped[IntegrationPlatform] = mapped_column(
        _enum(IntegrationPlatform, "integration_platform"), nullable=False
    )
    integration_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("integrations.id", ondelete="SET NULL"),
        nullable=True,
    )
    store_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("stores.id", ondelete="SET NULL"),
        nullable=True,
    )
    bling_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    external_order_id: Mapped[str] = mapped_column(Text, nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, server_default=text("'BRL'"))
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    settlement_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'posted'")
    )
    raw: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"), default=dict
    )
