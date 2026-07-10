from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field


class FaturaOut(BaseModel):
    id: UUID
    servico: str
    detalhes: str | None = None
    plano: str | None = None
    valor: Decimal | None = None
    data_vencimento: date
    created_by: UUID | None = None
    created_at: datetime
    updated_at: datetime


class FaturaCreate(BaseModel):
    servico: str = Field(min_length=1)
    detalhes: str | None = None
    plano: str | None = None
    valor: Decimal | None = None
    data_vencimento: date


class FaturaPatch(BaseModel):
    servico: str | None = Field(default=None, min_length=1)
    detalhes: str | None = None
    plano: str | None = None
    valor: Decimal | None = None
    data_vencimento: date | None = None
