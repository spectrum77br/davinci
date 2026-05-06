from datetime import time

from pydantic import BaseModel, ConfigDict, Field


class SettingsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    daily_sync_enabled: bool
    daily_sync_time: time | None = None
    sync_interval_minutes: int | None = None
    low_stock_threshold: int | None = None
    notify_email: bool
    notify_telegram: bool
    notify_daily_sync: bool
    telegram_chat_id: str | None = None


class SettingsPatchIn(BaseModel):
    daily_sync_enabled: bool | None = None
    daily_sync_time: time | None = None
    sync_interval_minutes: int | None = Field(default=None, ge=5, le=1440)
    low_stock_threshold: int | None = Field(default=None, ge=0)
    notify_email: bool | None = None
    notify_telegram: bool | None = None
    notify_daily_sync: bool | None = None
    telegram_chat_id: str | None = None
