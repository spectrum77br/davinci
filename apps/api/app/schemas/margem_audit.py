from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class MargemAuditOut(BaseModel):
    id: UUID
    created_at: datetime
    acao: str
    pedido_bling: str
    bling_id: str | None = None
    sku: str | None = None
    valor_antigo: str | None = None
    valor_novo: str | None = None
    origem: str
    mudado_por: UUID | None = None
    mudado_por_email: str | None = None
    mudado_por_nome: str | None = None


class MargemAuditPage(BaseModel):
    items: list[MargemAuditOut]
    total: int
    limit: int
    offset: int
