from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin
from app.models.enums import CellStatus, PricingPlatform


def _enum(py_enum, name: str):
    return Enum(
        py_enum,
        name=name,
        schema=None,
        create_type=False,
        values_callable=lambda x: [e.value for e in x],
    )


class PricingAccount(Base, TimestampMixin):
    __tablename__ = "pricing_accounts"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    platform: Mapped[PricingPlatform] = mapped_column(
        _enum(PricingPlatform, "pricing_platform"), nullable=False
    )
    listing_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    segment_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("segments.id", ondelete="RESTRICT"),
        nullable=False,
    )
    kit_number: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    commission: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    margin1: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    shipping1: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    margin2: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    shipping2: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    margin3: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    shipping3: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    margin4: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    shipping4: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    margin5: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    shipping5: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    server: Mapped[str | None] = mapped_column(String(64), nullable=True)
    email: Mapped[str | None] = mapped_column(String(256), nullable=True)
    password_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    shipping_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    return_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    observation: Mapped[str | None] = mapped_column(Text, nullable=True)
    observation2: Mapped[str | None] = mapped_column(Text, nullable=True)
    observation3: Mapped[str | None] = mapped_column(Text, nullable=True)
    store_info_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    integration_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("integrations.id", ondelete="SET NULL"),
        nullable=True,
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )


class PricingProduct(Base, TimestampMixin):
    __tablename__ = "pricing_products"
    __table_args__ = (
        UniqueConstraint("user_id", "sku", name="uq_pricing_products_user_sku"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    product_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="SET NULL"),
        nullable=True,
    )
    sku: Mapped[str] = mapped_column(String(2048), nullable=False)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    segment_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("segments.id", ondelete="RESTRICT"),
        nullable=False,
    )
    bling_cost_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    cost_kit1: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=Decimal("0"), server_default=text("0")
    )
    cost_kit2: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    cost_kit3: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    cost_kit4: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    description: Mapped[str | None] = mapped_column(String(256), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    ean: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    in_catalog: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )


class PricingOverride(Base, TimestampMixin):
    __tablename__ = "pricing_overrides"
    __table_args__ = (
        UniqueConstraint(
            "pricing_product_id",
            "pricing_account_id",
            name="uq_pricing_overrides_product_account",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    pricing_product_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("pricing_products.id", ondelete="CASCADE"),
        nullable=False,
    )
    pricing_account_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("pricing_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    price_override: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    cell_status: Mapped[CellStatus] = mapped_column(
        _enum(CellStatus, "cell_status"),
        nullable=False,
        default=CellStatus.AUTO,
        server_default=text("'auto'"),
    )


class AuditDismissedSku(Base):
    __tablename__ = "audit_dismissed_skus"
    __table_args__ = (
        UniqueConstraint("user_id", "sku", name="uq_audit_dismissed_skus_user_sku"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    sku: Mapped[str] = mapped_column(Text, nullable=False)
    dismissed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


class StoreInfo(Base, TimestampMixin):
    __tablename__ = "store_info"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    platform: Mapped[str] = mapped_column(String(64), nullable=False)
    segment: Mapped[str | None] = mapped_column(String(128), nullable=True)
    freight: Mapped[str | None] = mapped_column(String(128), nullable=True)
    cpf_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    account_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    server: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cnpj: Mapped[str | None] = mapped_column(String(32), nullable=True)
    email: Mapped[str | None] = mapped_column(String(256), nullable=True)
    observation: Mapped[str | None] = mapped_column(Text, nullable=True)
    shipping_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    return_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    password_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    link: Mapped[str | None] = mapped_column(Text, nullable=True)
    integration_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("integrations.id", ondelete="SET NULL"),
        nullable=True,
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    # Lojas screen extras (migration 0043).
    bling_store_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    upseseller: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    duoker: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    uf_restrictions: Mapped[list[str] | None] = mapped_column(
        ARRAY(Text), nullable=True
    )


class PricingPushIdempotency(Base):
    __tablename__ = "pricing_push_idempotency"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
