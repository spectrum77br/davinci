"""Schemas pra aba Faturamento — relatório por loja de pedidos faturáveis
(situacao em 6 Em aberto, 15 Em andamento, 83953 Entregue) lido da tabela
local bling_orders."""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field


class MargemFaixa(BaseModel):
    """As margens da aba Contas (margin1..5, fração: 0.425 = 42,5%) das contas
    de preço da loja: a menor e a maior (iguais = uma margem só)."""

    min: float
    max: float


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
    # M-22 (24/11/2026). A equipe de vendas da loja (store_info.sales_team:
    # 101/102/103; None = sem equipe ou loja sem cadastro).
    equipe: int | None = None
    # As contas da Tabela de Preços da loja (kit e catálogo): a tela soma os
    # cartões vermelhos delas na grade (coluna Pendências). Vazia = a loja não
    # tem conta na Tabela de Preços ("—").
    contas_preco: list[str] = Field(default_factory=list)
    # Gasto guardado (Ads + afiliados) no período; None = sem dado ("—").
    # `investimento_desde`: desde quando o gasto está guardado; `parcial`: o
    # período começa antes disso.
    investimento: float | None = None
    investimento_desde: date | None = None
    investimento_parcial: bool = False
    # SÓ para administrador: a API não manda este campo a quem não é ADM.
    margem: MargemFaixa | None = None


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
