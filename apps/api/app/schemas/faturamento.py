"""Schemas pra aba Faturamento — relatório por loja de pedidos faturáveis
(situacao em 6 Em aberto, 15 Em andamento, 83953 Entregue) lido da tabela
local bling_orders."""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field


class MargemReal(BaseModel):
    """A Margem da loja no período (M-37, 24/11/2026; regras em services/
    faturamento_margem.py): (sobrou − ads) ÷ custo, da Conciliação. Era a
    faixa menor–maior da aba Contas.

    `valor` em fração (0.252 = 25,2%); None = "—" (sem custo para dividir ou
    `antes_do_inicio`: o período começa antes de `inicio`, o início da
    Conciliação das contas da loja). `ads`: só a parte de Ads do Investimento
    (afiliados já saem do sobrou de cada pedido); `ads_com_dado` False = sem
    dado de Ads, conta 0; `ads_ate`: as semanas fechadas da Conferência vão
    até esse dia. As contagens vão para o balão: N pedidos = pagos + com
    valor previsto + cancelados + sem valor de venda (estes fora da conta)."""

    valor: float | None = None
    sobrou: float
    ads: float
    ads_com_dado: bool = False
    ads_ate: date | None = None
    custo: float
    pedidos: int
    pagos: int
    previstos: int
    cancelados: int
    sem_valor: int
    inicio: date | None = None
    antes_do_inicio: bool = False


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
    # M-36 (24/11/2026): afiliados + Ads do Marketing no período; None = sem
    # dado nenhum ("—", nunca zero). Os pedaços ao lado (None = aquele pedaço
    # sem dado): a tela mostra "Afiliados R$ X + Ads R$ Y" e a Margem (M-37)
    # desconta só o de Ads. `investimento_desde`: desde quando a loja tem
    # dado; `parcial`: o período começa antes disso. `investimento_ate`: o
    # último dia das semanas fechadas da Conferência, quando param antes do
    # fim do período (a semana corrente entra quando fechar).
    investimento: float | None = None
    investimento_afiliados: float | None = None
    investimento_ads: float | None = None
    investimento_desde: date | None = None
    investimento_parcial: bool = False
    investimento_ate: date | None = None
    # SÓ para administrador: a API não manda este campo a quem não é ADM.
    # M-37: None = a loja não tem conta nem pedido na Conciliação ("—").
    margem: MargemReal | None = None


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
