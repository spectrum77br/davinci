from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class SyncProductIn(BaseModel):
    product_id: UUID


class SyncAllIn(BaseModel):
    integration_ids: list[UUID] | None = None
    product_ids: list[UUID] | None = None


class SyncLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    job_id: UUID | None = None
    product_id: UUID | None = None
    product_link_id: UUID | None = None
    integration_id: UUID | None = None
    store_id: UUID | None = None
    platform: str | None = None
    action: str
    status: str
    qty_before: int | None = None
    qty_after: int | None = None
    error_code: str | None = None
    error_detail: str | None = None
    payload: dict


class SyncLogPage(BaseModel):
    items: list[SyncLogOut]
    total: int
    limit: int
    offset: int


class SyncLogStats(BaseModel):
    window_hours: int
    ok: int
    skipped: int
    retryable: int
    fatal: int
    requires_review: int
    by_platform: dict[str, dict[str, int]]
