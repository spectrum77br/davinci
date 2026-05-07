from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AuditUploadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    user_id: UUID
    filename: str
    size_bytes: int
    sheets: list[str]
    created_at: datetime


class AuditPreviewIn(BaseModel):
    upload_id: UUID
    sheet: str
    max_rows: int = Field(default=10, ge=1, le=50)


class AuditPreviewOut(BaseModel):
    sheet_name: str
    headers: list[str]
    sku_column: int | None
    rows: list[list[str | None]]
    total_rows: int
    suggested_account_map: dict[str, UUID] = {}


class AuditRunCreate(BaseModel):
    upload_id: UUID
    sheet: str
    account_map: dict[str, UUID]


class AuditRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    user_id: UUID
    upload_id: UUID
    job_id: UUID | None
    sheet_name: str
    account_map: dict
    status: str
    total: int
    processed: int
    summary: dict
    error: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


class AuditFindingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    run_id: UUID
    sku: str
    pricing_product_id: UUID | None
    pricing_account_id: UUID | None
    column_header: str | None
    expected_price: Decimal | None
    actual_price: Decimal | None
    status: str
    detail: str | None
    fixed: bool
    fixed_at: datetime | None
    created_at: datetime


class AuditFindingsPage(BaseModel):
    items: list[AuditFindingOut]
    total: int
    limit: int
    offset: int


class AuditFixIn(BaseModel):
    """Bulk-fix payload: pass either explicit `finding_ids` or filters
    (`run_id` + `status_in`) to fix every matching finding."""
    finding_ids: list[UUID] | None = None
    status_in: list[str] | None = None
    run_id: UUID | None = None


class AuditFixResult(BaseModel):
    fixed: int
    failed: int
    skipped: int
    details: list[dict] = []
