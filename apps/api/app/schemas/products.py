from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

SkuStr = Annotated[str, Field(min_length=1, max_length=120)]


class ProductLinkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    product_id: UUID
    integration_id: UUID
    store_id: UUID | None = None
    platform: str
    external_id: str
    variation_id: str | None = None
    external_sku: str | None = None
    listing_title: str | None = None
    stock: int | None = None
    price: Decimal | None = None
    last_sync_status: str
    last_sync_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    sku: str
    name: str
    category: str | None = None
    cost_price: Decimal | None = None
    bling_cost_price: Decimal | None = None
    price: Decimal | None = None
    stock: int
    min_stock: int
    bling_product_id: int | None = None
    integration_id: UUID | None = None
    image_url: str | None = None
    observation: str | None = None
    observation2: str | None = None
    observation3: str | None = None
    last_imported_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    links: list[ProductLinkOut] = Field(default_factory=list)


class ProductPage(BaseModel):
    items: list[ProductOut]
    total: int
    page: int
    page_size: int


class ProductCreate(BaseModel):
    sku: SkuStr
    name: str = Field(min_length=1)
    category: str | None = None
    cost_price: Decimal | None = None
    price: Decimal | None = None
    stock: int = 0
    min_stock: int = 0
    integration_id: UUID | None = None
    image_url: str | None = None
    observation: str | None = None
    observation2: str | None = None
    observation3: str | None = None

    @field_validator("sku")
    @classmethod
    def _strip_sku(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("sku must not be blank")
        return v


class ProductPatch(BaseModel):
    name: str | None = None
    category: str | None = None
    cost_price: Decimal | None = None
    price: Decimal | None = None
    stock: int | None = None
    min_stock: int | None = None
    image_url: str | None = None
    observation: str | None = None
    observation2: str | None = None
    observation3: str | None = None


class BulkDeleteIn(BaseModel):
    ids: list[UUID] = Field(min_length=1)


# ---------- Bling import ----------

class BlingPreviewItem(BaseModel):
    bling_product_id: int
    sku: str | None
    name: str
    cost_price: Decimal | None = None
    bling_cost_price: Decimal | None = None
    price: Decimal | None = None
    stock: int | None = None
    min_stock: int | None = None
    image_url: str | None = None
    category: str | None = None
    observation: str | None = None


class BlingPreviewOut(BaseModel):
    integration_id: UUID
    page: int
    items: list[BlingPreviewItem]


class BlingImportIn(BaseModel):
    integration_id: UUID
    bling_product_ids: list[int] = Field(min_length=1)


class BlingImportSummary(BaseModel):
    imported: int
    updated: int
    skipped_no_sku: list[int]


# ---------- Auto-link job ----------

class AutoLinkIn(BaseModel):
    integration_ids: list[UUID] | None = None


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    type: str
    status: str
    total: int
    processed: int
    payload: dict
    result: dict
    details: list
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    last_heartbeat_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class JobCreatedOut(BaseModel):
    job_id: UUID


class JobPage(BaseModel):
    items: list[JobOut]
    total: int
    limit: int
    offset: int


class JobStats(BaseModel):
    pending: int
    running: int
    succeeded: int
    failed: int
    cancelled: int
