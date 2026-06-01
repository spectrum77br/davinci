from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class SituacaoAuditOut(BaseModel):
    id: UUID
    created_at: datetime
    pedido_bling: str
    bling_id: str | None = None
    sku: str | None = None
    situacao_antiga: str | None = None
    situacao_nova: str
    origem: str
    mudado_por: UUID | None = None
    mudado_por_email: str | None = None
    mudado_por_nome: str | None = None


class SituacaoAuditPage(BaseModel):
    items: list[SituacaoAuditOut]
    total: int
    limit: int
    offset: int
