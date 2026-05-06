from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ListingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    integration_id: UUID
    platform: str
    external_id: str
    sku: str | None = None
    title: str
    description: str | None = None
    price: int | None = None
    stock: int | None = None
    status: str
    category: str | None = None
    thumbnail_url: str | None = None
    product_id: UUID | None = None
    raw_data: dict
    imported_at: datetime
    created_at: datetime
    updated_at: datetime


class ListingPage(BaseModel):
    items: list[ListingOut]
    total: int
    page: int
    page_size: int


class ListingPatch(BaseModel):
    sku: str | None = None
    product_id: UUID | None = None
    status: str | None = None
    category: str | None = None


class ListingImportIn(BaseModel):
    integration_id: UUID
    max_pages: int | None = Field(default=None, ge=1, le=100)


class ListingRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    platform: str
    sku: str | None = None
    product_name: str
    description: str | None = None
    requested_price: int | None = None
    category: str | None = None
    notes: str | None = None
    status: str
    created_at: datetime
    updated_at: datetime


class ListingRequestCreate(BaseModel):
    platform: str
    sku: str | None = None
    product_name: str = Field(min_length=1)
    description: str | None = None
    requested_price: int | None = None
    category: str | None = None
    notes: str | None = None


class ListingRequestPatch(BaseModel):
    sku: str | None = None
    product_name: str | None = None
    description: str | None = None
    requested_price: int | None = None
    category: str | None = None
    notes: str | None = None
    status: str | None = None
