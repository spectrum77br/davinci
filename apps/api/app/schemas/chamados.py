from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field


class ChamadoOut(BaseModel):
    id: UUID
    data: date | None = None
    pedido_bling: str | None = None
    pedido_marketplace: str | None = None
    plataforma: str | None = None
    conta: str | None = None
    meli_status: dict[str, str] = Field(default_factory=dict)
    localizacao: str | None = None
    status_bling: str | None = None
    observacao: str | None = None
    created_by: UUID | None = None
    created_at: datetime
    updated_at: datetime


class ChamadoCreate(BaseModel):
    data: date | None = None
    pedido_bling: str | None = None
    pedido_marketplace: str | None = None
    plataforma: str | None = None
    conta: str | None = None
    meli_status: dict[str, str] = Field(default_factory=dict)
    localizacao: str | None = None
    status_bling: str | None = None
    observacao: str | None = None


class ChamadoPatch(BaseModel):
    data: date | None = None
    pedido_bling: str | None = None
    pedido_marketplace: str | None = None
    plataforma: str | None = None
    conta: str | None = None
    meli_status: dict[str, str] | None = None
    localizacao: str | None = None
    status_bling: str | None = None
    observacao: str | None = None


class SugestaoIn(BaseModel):
    """Seleção parcial dos 8 campos de status do Meli."""

    meli_status: dict[str, str] = Field(default_factory=dict)


class CandidatoOut(BaseModel):
    status_bling: str
    matches: int


class SugestaoOut(BaseModel):
    candidatos: list[CandidatoOut]


class OpcoesOut(BaseModel):
    """Campos + opções distintas (pra popular os selects do formulário)."""

    field_order: list[str]
    field_labels: dict[str, str]
    field_options: dict[str, list[str]]
