"""Schemas pra aba Faturamento — relatório por loja de pedidos
ENTREGUES (situacao=83953) lido da tabela local bling_orders."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class FaturamentoLinha(BaseModel):
    """Uma linha do resumo por loja."""

    store_id: str
    loja: str | None = None
    tipo: str | None = None
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
