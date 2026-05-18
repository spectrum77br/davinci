from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin
from app.models.enums import (
    BackgroundJobStatus,
    BackgroundJobType,
    IntegrationPlatform,
    LinkSyncStatus,
)


def _enum(py_enum, name: str):
    return Enum(
        py_enum,
        name=name,
        schema=None,
        create_type=False,
        values_callable=lambda x: [e.value for e in x],
    )


class Product(Base, TimestampMixin):
    __tablename__ = "products"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    sku: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str | None] = mapped_column(Text, nullable=True)
    segment_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("segments.id", ondelete="SET NULL"),
        nullable=True,
    )
    cost_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), nullable=True)
    bling_cost_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), nullable=True)
    price: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), nullable=True)
    stock: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    min_stock: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    bling_product_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Bling product flags (char(1) in DB, populated by the importer):
    #   situacao: A=Ativo, E=Excluído, I=Inativo, NULL=desconhecido
    #   formato:  S=Simples, E=Composto/kit
    # Used by the audit to skip deleted products and to switch the cost
    # comparison to cost_kit1 when the source is a kit.
    situacao: Mapped[str | None] = mapped_column(String(1), nullable=True)
    formato: Mapped[str | None] = mapped_column(String(1), nullable=True)
    integration_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("integrations.id", ondelete="SET NULL"),
        nullable=True,
    )
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    observation: Mapped[str | None] = mapped_column(Text, nullable=True)
    observation2: Mapped[str | None] = mapped_column(Text, nullable=True)
    observation3: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_imported_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ProductCategory(Base, TimestampMixin):
    __tablename__ = "product_categories"
    __table_args__ = (CheckConstraint("length(trim(name)) > 0", name="name_not_blank"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    bling_category_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    parent_bling_category_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            "product_categories.bling_category_id",
            ondelete="SET NULL",
            name="fk_product_categories_parent_bling_cat_id",
        ),
        nullable=True,
    )


class ProductLink(Base, TimestampMixin):
    __tablename__ = "product_links"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    product_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
    )
    integration_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("integrations.id", ondelete="CASCADE"),
        nullable=False,
    )
    store_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("stores.id", ondelete="SET NULL"),
        nullable=True,
    )
    platform: Mapped[IntegrationPlatform] = mapped_column(
        _enum(IntegrationPlatform, "integration_platform"),
        nullable=False,
    )
    external_id: Mapped[str] = mapped_column(Text, nullable=False)
    variation_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    external_sku: Mapped[str | None] = mapped_column(Text, nullable=True)
    listing_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    listing_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stock: Mapped[int | None] = mapped_column(Integer, nullable=True)
    price: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), nullable=True)
    last_sync_status: Mapped[LinkSyncStatus] = mapped_column(
        _enum(LinkSyncStatus, "link_sync_status"),
        nullable=False,
        default=LinkSyncStatus.PENDING,
        server_default=text("'pending'"),
    )
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class BackgroundJob(Base, TimestampMixin):
    __tablename__ = "background_jobs"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    type: Mapped[BackgroundJobType] = mapped_column(
        _enum(BackgroundJobType, "background_job_type"),
        nullable=False,
    )
    status: Mapped[BackgroundJobStatus] = mapped_column(
        _enum(BackgroundJobStatus, "background_job_status"),
        nullable=False,
        default=BackgroundJobStatus.PENDING,
        server_default=text("'pending'"),
    )
    created_by: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    arq_job_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    total: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    processed: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    result: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    details: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
