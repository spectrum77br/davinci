"""Aba Faturamento — relatório por loja de pedidos ENTREGUES.

Fonte LOCAL: davinci.bling_orders. Não chama API do Bling.

Definições fixadas com o operador:
- Só situacao = '83953' (Entregue).
- bling_orders tem UMA LINHA POR ITEM (item_index), e `total` se repete em
  cada linha do mesmo pedido. Pra somar o faturamento sem inflar é
  obrigatório DEDUPLICAR por pedido primeiro: max(total) GROUP BY bling_id.
- Agrupamento por LOJA usa `bling_orders.loja` (id da loja no Bling), NÃO o
  FK store_id. Motivo: ~16k pedidos entregues têm store_id NULL (a ingestão
  nem sempre vincula a Store), e um INNER join em Store descartava esses
  pedidos — escondendo ~R$24M de receita real. `loja` está preenchido em
  quase 100% dos pedidos e casa 1:1 com store_info.bling_store_id.
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

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_permission, user_scope
from app.models import BlingOrder, StoreInfo, User, UserRole
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
    team: int | None = Query(None),
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

    # Equipes que ESTE usuário pode filtrar. Admin → todas as equipes com
    # loja cadastrada em store_info. Não-admin → suas próprias sales_teams
    # (mesma base do user_scope, que já restringe as linhas visíveis).
    if user.role == UserRole.ADMIN:
        teams_avail = (
            (
                await session.execute(
                    select(StoreInfo.sales_team)
                    .where(StoreInfo.sales_team.isnot(None))
                    .distinct()
                    .order_by(StoreInfo.sales_team)
                )
            )
            .scalars()
            .all()
        )
    else:
        teams_avail = sorted(user.sales_teams or [])

    # Filtro explícito de equipe: só vale se o usuário tem acesso a ela.
    if team is not None and team not in teams_avail:
        raise HTTPException(status_code=403, detail={"code": "team_not_allowed"})

    # user_scope já restringe ao escopo do usuário; `team` afunila mais.
    scope_clauses = [user_scope(StoreInfo, user)]
    if team is not None:
        scope_clauses.append(StoreInfo.sales_team == team)

    # CTE com max(total) por bling_id pra deduplicar — bling_orders tem uma
    # linha por item, total repete. Carrega `loja` (id da loja Bling) como
    # chave do relatório; store_id FK é NULL em ~16k entregues e dropava
    # esses pedidos num INNER join.
    ord_cte = (
        select(
            BlingOrder.bling_id.label("bling_id"),
            BlingOrder.loja.label("bling_store_id"),
            func.max(BlingOrder.total).label("total"),
        )
        .where(
            BlingOrder.situacao == _SITUACAO_ENTREGUE,
            BlingOrder.data >= start,
            BlingOrder.data < end,
        )
        .group_by(BlingOrder.bling_id, BlingOrder.loja)
        .cte("ord")
    )

    stmt = (
        select(
            ord_cte.c.bling_store_id.label("bling_store_id"),
            StoreInfo.platform.label("tipo"),
            StoreInfo.account_name.label("loja"),
            StoreInfo.sales_team.label("sales_team"),
            func.count().label("pedidos"),
            func.coalesce(
                func.round(func.sum(ord_cte.c.total), 2), 0,
            ).label("faturamento"),
        )
        .select_from(ord_cte)
        # LEFT JOIN em store_info pelo id da loja Bling — recupera nome/
        # plataforma/equipe quando cadastrada; pedidos de loja sem cadastro
        # entram com tipo/loja NULL e AINDA contam no total. bling_store_id é
        # único em store_info, então não há fan-out. Pro escopo por equipe, o
        # where(user_scope) remove linhas sem store_info quando o usuário tem
        # sales_teams (LEFT vira INNER na prática), igual antes.
        .outerjoin(
            StoreInfo,
            StoreInfo.bling_store_id == ord_cte.c.bling_store_id,
        )
        .where(*scope_clauses)
        .group_by(
            ord_cte.c.bling_store_id,
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
        # COALESCE pra exibição: account_name → platform → id da loja Bling.
        loja_label = (
            r.loja
            or r.tipo
            or (str(r.bling_store_id) if r.bling_store_id is not None else None)
            or "Sem cadastro"
        )
        itens.append(FaturamentoLinha(
            # Chave única da linha = id da loja Bling (sentinel pros poucos
            # pedidos sem `loja`). Não é mais o UUID de Store.
            store_id=(
                str(r.bling_store_id) if r.bling_store_id is not None else "sem-loja"
            ),
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
        teams=list(teams_avail),
        team=team,
    )
