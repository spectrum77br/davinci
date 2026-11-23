from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    type: str
    severity: str
    title: str
    message: str | None = None
    payload: dict[str, Any] = {}
    # O "lido" de QUEM PEDIU (M-19: por pessoa em `alerta_leituras`).
    read_at: datetime | None = None
    created_at: datetime
    # M-19 (23/11/2026): quem vê e de qual loja é.
    escopo: str = "adm"
    # A equipe de vendas da loja agora (101/102/103); só nos de equipe.
    equipe: int | None = None
    plataforma: str | None = None
    conta: str | None = None
    integration_id: UUID | None = None
    # Rótulo da coluna Tipo (Falha sync, Revisão, Banido, WhatsApp...).
    tipo: str | None = None


class AlertListOut(BaseModel):
    items: list[AlertOut]
    # Com os filtros da barra, sem o do Layout (Todos/Não lidos/Lidos):
    # o "N não lidos · N total" da tela.
    total: int
    unread: int
    # Com todos os filtros (inclusive o Layout): a conta da paginação.
    encontrados: int = 0


class UnreadCountOut(BaseModel):
    unread: int


class AlertOpcaoConta(BaseModel):
    id: UUID
    nome: str
    plataforma: str | None = None
    equipe: int | None = None


class AlertOpcoesOut(BaseModel):
    # 'adm', '101', '102', '103', 'pessoal' — só o que a pessoa pode ver.
    equipes: list[str]
    plataformas: list[str]
    contas: list[AlertOpcaoConta]


class LastDailySyncOut(BaseModel):
    job_id: UUID | None = None
    status: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    total: int = 0
    processed: int = 0
    result: dict[str, Any] = {}


class MarkReadOut(BaseModel):
    updated: int
