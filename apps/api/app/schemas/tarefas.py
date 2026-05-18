from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field


class TarefaOut(BaseModel):
    id: UUID
    responsavel_id: UUID
    responsavel_name: str | None = None
    responsavel_email: str | None = None
    data_inicio: date
    data_conclusao: date | None = None
    tarefa: str
    observacao: str | None = None
    created_by: UUID | None = None
    created_at: datetime
    updated_at: datetime


class TarefaCreate(BaseModel):
    responsavel_id: UUID
    data_inicio: date
    tarefa: str = Field(min_length=1)


class TarefaPatch(BaseModel):
    # Admin: all fields settable. Non-admin: only observacao is honored
    # (router enforces this — other fields are silently ignored when set
    # by a non-admin so the request shape stays uniform).
    responsavel_id: UUID | None = None
    data_inicio: date | None = None
    data_conclusao: date | None = None
    tarefa: str | None = Field(default=None, min_length=1)
    observacao: str | None = None
