from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class IntegrationOut(BaseModel):
    """Public projection — never includes credentials."""
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    user_id: UUID
    store_id: UUID | None = None
    company_id: UUID | None = None
    platform: str
    name: str
    status: str
    token_expires_at: datetime | None = None
    last_test_at: datetime | None = None
    last_test_ok: bool | None = None
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime


class IntegrationCreate(BaseModel):
    store_id: UUID
    platform: str
    name: str
    credentials: dict


class IntegrationPatch(BaseModel):
    name: str | None = None
    status: str | None = None
    credentials: dict | None = None


class TestConnectionOut(BaseModel):
    ok: bool
    detail: str | None = None
    info: dict | None = None


class OAuthStartOut(BaseModel):
    url: str
    state: str


class BlingStoreOut(BaseModel):
    id: int
    nome: str | None = None
    descricao: str | None = None


class BlingStoresOut(BaseModel):
    items: list[BlingStoreOut]
