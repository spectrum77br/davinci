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


class ListingResumo(BaseModel):
    """Cartões da aba Anúncios (Produtos › Anúncios, 13/11/2026): contam o
    departamento escolhido inteiro (escopo + departamento), sem os filtros de
    busca/conta/canal/status da tela."""

    total: int = 0
    sem_produto: int = 0
    ativos: int = 0
    em_revisao: int = 0


class ListingPage(BaseModel):
    items: list[ListingOut]
    total: int
    page: int
    page_size: int
    resumo: ListingResumo | None = None


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
