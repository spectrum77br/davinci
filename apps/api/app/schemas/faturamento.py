"""Schemas pra aba Faturamento — relatório por loja de pedidos faturáveis
(situacao em 6 Em aberto, 15 Em andamento, 83953 Entregue) lido da tabela
local bling_orders."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class FaturamentoLinha(BaseModel):
    """Uma linha do resumo por loja."""

    store_id: str
    loja: str | None = None
    tipo: str | None = None
    # Classificação de produtos da loja. `tipo` segue sendo a plataforma
    # legada para preservar os consumidores existentes da API.
    departments: list[str] = Field(default_factory=list)
    pedidos: int
    faturamento: float
    ticket_medio: float


class FaturamentoOut(BaseModel):
    itens: list[FaturamentoLinha]
    total_pedidos: int
    total_faturamento: float
    start: datetime
    end: datetime
    # Equipes que o usuário pode filtrar (admin → todas as equipes com loja
    # cadastrada; não-admin → suas sales_teams). `team` = filtro aplicado.
    teams: list[int] = []
    team: int | None = None
