"""Bling-as-revenue-source for the Marketing module.

Faturamento = a receita "faturável" da empresa, exatamente como a aba
Faturamento do DaVinci (routers/faturamento.py) define: pedidos em
`davinci.bling_orders` cuja situação está em {6 Em aberto, 15 Em andamento,
83953 Entregue}. Fonte LOCAL (a MESMA tabela que a aba Faturamento usa) —
NÃO chama a API do Bling. Assim os números do painel de Marketing batem
1:1 com a aba Faturamento.

bling_orders tem UMA LINHA POR ITEM (`total` se repete em cada linha do
mesmo pedido); pra somar sem inflar é obrigatório deduplicar por pedido:
MAX(total) GROUP BY bling_id — idêntico ao que faturamento.py faz.

Agregamos por DIA (data do pedido em BRT) pra o orquestrador casar contra o
gasto diário de anúncios e calcular o ACOS "real" (gasto / faturamento).

Loja routing (inalterado):
  1. Integration.bling_loja_id (override explícito), senão
  2. Store.bling_store_id (via Integration.store_id), senão
  3. None → caller trata como "sem faturamento p/ essa integração".
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID

import structlog
from sqlalchemy import Date, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlingOrder
from app.models.company import Store
from app.models.integration import Integration

logger = structlog.get_logger()

# Situações Bling que contam como faturamento — MESMA definição da aba
# Faturamento (routers/faturamento.py):
#   '6'     → Em aberto
#   '15'    → Em andamento
#   '83953' → Entregue
# Etiqueta gerada, em rota, devolução, cancelado… NÃO contam.
_SITUACOES_FATURAVEIS: tuple[str, ...] = ("6", "15", "83953")

# Fuso do negócio — o "dia" do faturamento é o dia BRT do pedido.
_BRT = "America/Sao_Paulo"


@dataclass(slots=True)
class BlingRevenue:
    """Faturamento por dia (BRL), keyed pela data BRT do pedido. `total` é a
    soma da janela; `order_count`, o nº de pedidos distintos."""

    total: float
    by_day: dict[date, float]
    order_count: int


async def resolve_bling_loja_id(
    session: AsyncSession, integration: Integration
) -> int | None:
    """Retorna o idLoja Bling desta integração, ou None se não há mapeamento
    (nem override, nem Store.bling_store_id)."""
    if integration.bling_loja_id is not None:
        return int(integration.bling_loja_id)
    if integration.store_id is None:
        return None
    store = await session.get(Store, integration.store_id)
    if store is None or store.bling_store_id is None:
        return None
    return int(store.bling_store_id)


async def get_bling_revenue(
    session: AsyncSession,
    integration: Integration,
    *,
    start: date,
    end: date,
) -> BlingRevenue | None:
    """Faturamento faturável (situação ∈ {6,15,83953}) da loja desta
    integração, agregado por dia BRT em [start, end]. Fonte: tabela local
    davinci.bling_orders (a mesma da aba Faturamento). Dedup por pedido
    (MAX(total) GROUP BY bling_id) pra não inflar com as linhas-por-item.

    Retorna None quando a integração não tem loja Bling mapeada — o caller
    trata como "sem faturamento" (revenue 0 / ACOS None)."""
    loja_id = await resolve_bling_loja_id(session, integration)
    if loja_id is None:
        return None

    # Pré-filtro INDEXÁVEL na coluna crua `data` (timestamptz). BRT = UTC-3
    # (sem horário de verão desde 2019); a margem de ±1 dia cobre o offset
    # com folga. O bucketing exato por dia BRT é feito abaixo.
    start_dt = datetime.combine(start - timedelta(days=1), time.min, tzinfo=UTC)
    end_dt = datetime.combine(end + timedelta(days=2), time.min, tzinfo=UTC)

    # Dia BRT do pedido (bling_orders.data é timestamptz em UTC).
    brt_day = cast(func.timezone(_BRT, BlingOrder.data), Date)

    # ETAPA 1 — dedup por pedido: UMA linha por bling_id, com seu dia e total.
    per_order = (
        select(
            BlingOrder.bling_id.label("bling_id"),
            func.min(brt_day).label("day"),
            func.max(BlingOrder.total).label("total"),
        )
        .where(
            BlingOrder.loja == str(loja_id),
            BlingOrder.situacao.in_(_SITUACOES_FATURAVEIS),
            BlingOrder.data >= start_dt,
            BlingOrder.data < end_dt,
        )
        .group_by(BlingOrder.bling_id)
        .subquery()
    )

    # ETAPA 2 — soma o faturamento por dia.
    stmt = select(
        per_order.c.day.label("day"),
        func.coalesce(func.sum(per_order.c.total), 0).label("revenue"),
        func.count().label("orders"),
    ).group_by(per_order.c.day)

    by_day: dict[date, float] = {}
    total = 0.0
    count = 0
    for row in (await session.execute(stmt)).all():
        # Descarta os dias-borda que o pré-filtro cru deixou passar.
        if row.day is None or row.day < start or row.day > end:
            continue
        rev = float(row.revenue or 0)
        by_day[row.day] = rev
        total += rev
        count += int(row.orders or 0)

    return BlingRevenue(total=round(total, 2), by_day=by_day, order_count=count)


async def get_bling_revenue_by_integration_id(
    session: AsyncSession,
    integration_id: UUID,
    *,
    start: date,
    end: date,
) -> BlingRevenue | None:
    """Conveniência para callers que só têm o id da integração (endpoint de
    trigger manual, cron do worker). Carrega a Integration e delega."""
    integ = await session.get(Integration, integration_id)
    if integ is None:
        return None
    return await get_bling_revenue(session, integ, start=start, end=end)
