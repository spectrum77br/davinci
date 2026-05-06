from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    type: str
    severity: str
    title: str
    message: str | None = None
    payload: dict[str, Any] = {}
    read_at: datetime | None = None
    created_at: datetime


class AlertListOut(BaseModel):
    items: list[AlertOut]
    total: int
    unread: int


class UnreadCountOut(BaseModel):
    unread: int


class LastDailySyncOut(BaseModel):
    job_id: UUID | None = None
    status: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    total: int = 0
    processed: int = 0
    result: dict[str, Any] = {}


class MarkReadOut(BaseModel):
    updated: int
