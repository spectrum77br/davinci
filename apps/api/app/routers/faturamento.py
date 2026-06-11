"""Aba Faturamento — relatório por loja de pedidos ENTREGUES.

Fonte LOCAL: davinci.bling_orders. Não chama API do Bling.

Definições fixadas com o operador:
- Só situacao = '83953' (Entregue).
- bling_orders tem UMA LINHA POR ITEM (item_index), e `total` se repete em
  cada linha do mesmo pedido. Pra somar o faturamento sem inflar é
  obrigatório DEDUPLICAR por pedido primeiro: max(total) GROUP BY bling_id.
- Escopo por equipe via user_scope(StoreInfo, user). Admin vê tudo;
  usuário com sales_teams vê só lojas em store_info cuja sales_team está
  na lista; usuário sem equipe vê tudo (comportamento atual da Fase 1).
- Período: default últimos 90 dias.

Hoje nenhuma store_info tem sales_team preenchido — usuário não-admin
com equipe verá o relatório vazio até o admin atribuir equipes às
lojas. Isso é o comportamento correto, não bug.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Text, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_permission, user_scope
from app.models import BlingOrder, StoreInfo, User
from app.models.company import Store
from app.schemas.faturamento import FaturamentoLinha, FaturamentoOut

router = APIRouter(prefix="/api/faturamento", tags=["faturamento"])

# Bling situação ID — pedido entregue ao cliente. Único status que conta
# pra faturamento da empresa (ignora etiqueta gerada, em rota, devolução).
_SITUACAO_ENTREGUE = "83953"


@router.get("", response_model=FaturamentoOut)
async def list_faturamento(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("faturamento", "view"))],
    start: datetime | None = Query(None),
    end: datetime | None = Query(None),
) -> FaturamentoOut:
    """Resumo por loja, no período, dos pedidos entregues.

    SQL espelhado em apps/api/app/routers/faturamento.py (CTE `ord`
    deduplica por bling_id antes de agregar). user_scope(StoreInfo, user)
    aplica o filtro de equipe — admin pula, equipe restringe.
    """
    if end is None:
        end = datetime.now(UTC)
    if start is None:
        start = end - timedelta(days=90)

    # CTE com max(total) por bling_id pra deduplicar — bling_orders tem
    # uma linha por item, total repete.
    ord_cte = (
        select(
            BlingOrder.bling_id.label("bling_id"),
            BlingOrder.store_id.label("store_id"),
            func.max(BlingOrder.total).label("total"),
        )
        .where(
            BlingOrder.situacao == _SITUACAO_ENTREGUE,
            BlingOrder.data >= start,
            BlingOrder.data < end,
        )
        .group_by(BlingOrder.bling_id, BlingOrder.store_id)
        .cte("ord")
    )

    stmt = (
        select(
            Store.id.label("store_id"),
            Store.bling_store_id.label("bling_store_id"),
            StoreInfo.platform.label("tipo"),
            StoreInfo.account_name.label("loja"),
            StoreInfo.sales_team.label("sales_team"),
            func.count().label("pedidos"),
            func.coalesce(
                func.round(func.sum(ord_cte.c.total), 2), 0,
            ).label("faturamento"),
        )
        .select_from(ord_cte)
        .join(Store, ord_cte.c.store_id == Store.id)
        # LEFT JOIN: lojas sem store_info entram (com tipo/loja NULL). Pro
        # filtro de escopo por equipe, o LEFT JOIN vira INNER na prática
        # quando user.sales_teams está set — lojas sem store_info ou com
        # sales_team fora da lista são removidas pelo where do user_scope.
        .outerjoin(
            StoreInfo,
            StoreInfo.bling_store_id == cast(Store.bling_store_id, Text),
        )
        .where(user_scope(StoreInfo, user))
        .group_by(
            Store.id,
            Store.bling_store_id,
            StoreInfo.platform,
            StoreInfo.account_name,
            StoreInfo.sales_team,
        )
        .order_by(func.sum(ord_cte.c.total).desc().nulls_last())
    )

    rows = (await session.execute(stmt)).all()

    itens: list[FaturamentoLinha] = []
    total_pedidos = 0
    total_faturamento = 0.0
    for r in rows:
        ped = int(r.pedidos or 0)
        fat = float(r.faturamento or 0)
        # COALESCE pra exibição: account_name → platform → bling_store_id.
        loja_label = (
            r.loja
            or r.tipo
            or (str(r.bling_store_id) if r.bling_store_id is not None else None)
            or "Sem cadastro"
        )
        itens.append(FaturamentoLinha(
            store_id=str(r.store_id),
            loja=loja_label,
            tipo=r.tipo,
            pedidos=ped,
            faturamento=round(fat, 2),
            ticket_medio=round(fat / ped, 2) if ped else 0.0,
        ))
        total_pedidos += ped
        total_faturamento += fat

    return FaturamentoOut(
        itens=itens,
        total_pedidos=total_pedidos,
        total_faturamento=round(total_faturamento, 2),
        start=start,
        end=end,
    )
