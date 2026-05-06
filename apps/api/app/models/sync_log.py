from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.enums import IntegrationPlatform, LinkSyncStatus, SyncLogAction


def _enum(py_enum, name: str):
    return Enum(
        py_enum,
        name=name,
        schema=None,
        create_type=False,
        values_callable=lambda x: [e.value for e in x],
    )


class SyncLog(Base):
    """Partitioned by `created_at` (monthly). PK is (id, created_at) per Postgres
    declarative partitioning rules. See migration 0005_sync_logs.
    """

    __tablename__ = "sync_logs"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        primary_key=True,
        server_default=text("now()"),
        nullable=False,
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    job_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("background_jobs.id", ondelete="SET NULL"),
        nullable=True,
    )
    product_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="SET NULL"),
        nullable=True,
    )
    product_link_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("product_links.id", ondelete="SET NULL"),
        nullable=True,
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
    platform: Mapped[IntegrationPlatform | None] = mapped_column(
        _enum(IntegrationPlatform, "integration_platform"),
        nullable=True,
    )
    action: Mapped[SyncLogAction] = mapped_column(
        _enum(SyncLogAction, "sync_log_action"),
        nullable=False,
    )
    status: Mapped[LinkSyncStatus] = mapped_column(
        _enum(LinkSyncStatus, "link_sync_status"),
        nullable=False,
    )
    qty_before: Mapped[int | None] = mapped_column(Integer, nullable=True)
    qty_after: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
