from datetime import time
from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, Integer, Text, Time, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class UserSettings(Base, TimestampMixin):
    __tablename__ = "user_settings"

    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    daily_sync_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("FALSE")
    )
    daily_sync_time: Mapped[time | None] = mapped_column(Time(), nullable=True)
    sync_interval_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    low_stock_threshold: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notify_email: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("TRUE")
    )
    notify_telegram: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("FALSE")
    )
    notify_daily_sync: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("TRUE")
    )
    telegram_chat_id: Mapped[str | None] = mapped_column(Text, nullable=True)
