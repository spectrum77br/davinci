"""Faturamento da empresa no mês — a BASE das notas de percentual (30/09/2026).

Eduardo: "faz com base no faturamento já" (base = "vendas totais da empresa":
todas as lojas/marketplaces dela). A tela já traz a base preenchida com isto e a
pessoa pode trocar antes de emitir.

Mesma régua da aba Faturamento (routers/faturamento.py), para os números
baterem com o que ele vê lá:
- pedidos de `davinci.bling_orders` em situação 6 (Em aberto), 15 (Em
  andamento) ou 83953 (Entregue);
- UMA LINHA POR ITEM no espelho: soma sem inflar = max(total) por pedido;
- loja do pedido (`bling_orders.loja`) = `store_info.bling_store_id`.
A empresa são as lojas cadastradas com o CNPJ dela (Cadastros › Lojas). O mês
é o do calendário de São Paulo (data do pedido).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlingOrder, Company, StoreInfo

SITUACOES_FATURAVEIS = ("6", "15", "83953")  # igual à aba Faturamento
SP = ZoneInfo("America/Sao_Paulo")
CENTAVO = Decimal("0.01")


@dataclass
class Loja:
    plataforma: str
    conta: str | None
    pedidos: int
    valor: Decimal


@dataclass
class Faturamento:
    valor: Decimal = Decimal("0.00")
    pedidos: int = 0
    lojas: list[Loja] = field(default_factory=list)

    def resumo(self) -> dict:
        return {
            "valor": f"{self.valor:.2f}",
            "pedidos": self.pedidos,
            "lojas": [
                {
                    "plataforma": x.plataforma,
                    "conta": x.conta,
                    "pedidos": x.pedidos,
                    "valor": f"{x.valor:.2f}",
                }
                for x in self.lojas
            ],
        }


def _digitos(v: str | None) -> str:
    return "".join(ch for ch in (v or "") if ch.isdigit())


def periodo(competencia: date) -> tuple[datetime, datetime]:
    """[1º dia do mês, 1º dia do mês seguinte) no horário de São Paulo."""
    ini = datetime(competencia.year, competencia.month, 1, tzinfo=SP)
    ano, mes = (
        (competencia.year + 1, 1)
        if competencia.month == 12
        else (
            competencia.year,
            competencia.month + 1,
        )
    )
    return ini, datetime(ano, mes, 1, tzinfo=SP)


async def faturamento_das_empresas(
    session: AsyncSession, competencia: date, company_ids: list[UUID] | None = None
) -> dict[UUID, Faturamento]:
    """Faturamento do mês por empresa (só as que têm loja com o CNPJ dela).
    Empresa sem loja cadastrada não aparece no resultado."""
    empresas = (
        await session.execute(
            select(Company.id, Company.cnpj).where(
                Company.id.in_(company_ids) if company_ids is not None else true()
            )
        )
    ).all()
    por_cnpj: dict[str, UUID] = {}
    for cid, cnpj in empresas:
        d = _digitos(cnpj)
        if d:
            por_cnpj[d] = cid
    if not por_cnpj:
        return {}

    lojas = (
        await session.execute(
            select(
                StoreInfo.cnpj, StoreInfo.bling_store_id, StoreInfo.platform, StoreInfo.account_name
            ).where(StoreInfo.bling_store_id.is_not(None), StoreInfo.cnpj.is_not(None))
        )
    ).all()
    loja_empresa: dict[str, tuple[UUID, str, str | None]] = {}
    for cnpj, bling_store_id, plataforma, conta in lojas:
        cid = por_cnpj.get(_digitos(cnpj))
        loja_id = (bling_store_id or "").strip()
        if cid is not None and loja_id:
            loja_empresa[loja_id] = (cid, plataforma, conta)
    if not loja_empresa:
        return {}

    ini, fim = periodo(competencia)
    pedidos = (
        select(
            BlingOrder.bling_id,
            BlingOrder.loja.label("loja"),
            func.max(BlingOrder.total).label("total"),
        )
        .where(
            BlingOrder.situacao.in_(SITUACOES_FATURAVEIS),
            BlingOrder.data >= ini,
            BlingOrder.data < fim,
            BlingOrder.loja.in_(list(loja_empresa)),
        )
        .group_by(BlingOrder.bling_id, BlingOrder.loja)
        .cte("pedidos")
    )
    linhas = (
        await session.execute(
            select(
                pedidos.c.loja,
                func.count().label("n"),
                func.coalesce(func.sum(pedidos.c.total), 0).label("valor"),
            ).group_by(pedidos.c.loja)
        )
    ).all()

    out: dict[UUID, Faturamento] = {}
    vendas = {loja: (n, Decimal(str(valor))) for loja, n, valor in linhas}
    # Toda empresa com loja aparece, mesmo sem venda no mês (valor 0).
    for loja_id, (cid, plataforma, conta) in sorted(
        loja_empresa.items(), key=lambda x: (x[1][1] or "", x[1][2] or "")
    ):
        n, valor = vendas.get(loja_id, (0, Decimal(0)))
        f = out.setdefault(cid, Faturamento())
        f.lojas.append(Loja(plataforma, conta, int(n), valor.quantize(CENTAVO)))
        f.pedidos += int(n)
        f.valor = (f.valor + valor).quantize(CENTAVO)
    return out


async def faturamento_da_empresa(
    session: AsyncSession, company_id: UUID, competencia: date
) -> Faturamento | None:
    """None = a empresa não tem loja cadastrada com o CNPJ dela."""
    return (await faturamento_das_empresas(session, competencia, [company_id])).get(company_id)
