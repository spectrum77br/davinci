from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class AutomacaoOut(BaseModel):
    id: UUID
    nome: str
    descricao: str | None = None
    frequencia: str | None = None
    categoria: str | None = None
    ativa: bool
    created_by: UUID | None = None
    created_at: datetime
    updated_at: datetime


class AutomacaoCreate(BaseModel):
    nome: str = Field(min_length=1)
    descricao: str | None = None
    frequencia: str | None = None
    categoria: str | None = None
    ativa: bool = True


class AutomacaoPatch(BaseModel):
    nome: str | None = Field(default=None, min_length=1)
    descricao: str | None = None
    frequencia: str | None = None
    categoria: str | None = None
    ativa: bool | None = None
