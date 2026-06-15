"""Aba Faturamento — relatório por loja de pedidos ENTREGUES.

Fonte LOCAL: davinci.bling_orders. Não chama API do Bling.

Definições fixadas com o operador:
- Só situacao = '83953' (Entregue).
- bling_orders tem UMA LINHA POR ITEM (item_index), e `total` se repete em
  cada linha do mesmo pedido. Pra somar o faturamento sem inflar é
  obrigatório DEDUPLICAR por pedido primeiro: max(total) GROUP BY bling_id.
- O relatório PARTE DO ROSTER de lojas (store_info), NÃO dos pedidos: toda
  loja do escopo aparece, mesmo SEM venda no período (zerada). Os pedidos,
  agregados por `bling_orders.loja` (id da loja Bling, casa 1:1 com
  store_info.bling_store_id), entram via LEFT JOIN — loja sem pedido = 0.
- Pedidos de loja SEM cadastro em store_info (loja órfã) ainda contam, mas
  só na visão SEM escopo (admin/usuário sem equipe, sem filtro de equipe),
  como linha "Sem cadastro" — preserva a receita órfã no total sem furar o
  escopo de quem é restrito a equipes (órfão não tem equipe).
- Escopo por equipe via user_scope(StoreInfo, user) + filtro `team`. Admin
  vê tudo; usuário com sales_teams vê só lojas da(s) sua(s) equipe(s).
- Período: default últimos 90 dias.
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

    Parte do roster de lojas (store_info) — toda loja do escopo aparece,
    zerada se não vendeu — e faz LEFT JOIN nos pedidos agregados. Pedido de
    loja sem cadastro entra como "Sem cadastro" só na visão sem escopo.
    user_scope(StoreInfo, user) + `team` aplicam o filtro de equipe.
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

    # ETAPA 1 — pedidos do período: dedup por bling_id (bling_orders tem uma
    # linha por item, `total` repete) e agrega por loja Bling (pedidos +
    # faturamento). `agg` fica com UMA linha por bling_store_id.
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
    agg_cte = (
        select(
            ord_cte.c.bling_store_id.label("bling_store_id"),
            func.count().label("pedidos"),
            func.coalesce(
                func.round(func.sum(ord_cte.c.total), 2), 0,
            ).label("faturamento"),
        )
        .group_by(ord_cte.c.bling_store_id)
        .cte("agg")
    )

    # ETAPA 2 — roster de lojas do escopo. PARTE de store_info (não dos
    # pedidos) pra TODA loja aparecer, mesmo zerada. LEFT JOIN nos pedidos
    # agregados; loja sem venda fica com pedidos/faturamento = 0. Chave =
    # store_info.id (UUID), única até p/ as ~11 lojas sem bling_store_id.
    # bling_store_id é único em store_info, então o join não faz fan-out.
    roster_stmt = (
        select(
            StoreInfo.id.label("store_uuid"),
            StoreInfo.bling_store_id.label("bling_store_id"),
            StoreInfo.platform.label("tipo"),
            StoreInfo.account_name.label("loja"),
            func.coalesce(agg_cte.c.pedidos, 0).label("pedidos"),
            func.coalesce(agg_cte.c.faturamento, 0).label("faturamento"),
        )
        .select_from(StoreInfo)
        .outerjoin(
            agg_cte, agg_cte.c.bling_store_id == StoreInfo.bling_store_id
        )
        .where(*scope_clauses)
    )

    itens: list[FaturamentoLinha] = []
    total_pedidos = 0
    total_faturamento = 0.0
    for r in (await session.execute(roster_stmt)).all():
        ped = int(r.pedidos or 0)
        fat = float(r.faturamento or 0)
        # Exibição: account_name → platform → id da loja Bling → "Sem nome".
        loja_label = (
            r.loja
            or r.tipo
            or (str(r.bling_store_id) if r.bling_store_id is not None else None)
            or "Sem nome"
        )
        itens.append(FaturamentoLinha(
            store_id=str(r.store_uuid),
            loja=loja_label,
            tipo=r.tipo,
            pedidos=ped,
            faturamento=round(fat, 2),
            ticket_medio=round(fat / ped, 2) if ped else 0.0,
        ))
        total_pedidos += ped
        total_faturamento += fat

    # ETAPA 3 — pedidos órfãos (loja sem cadastro em store_info). Só na visão
    # SEM escopo (admin/usuário sem equipe) e SEM filtro de equipe: órfão não
    # tem equipe, então some quando se restringe a uma. Mantém a receita órfã
    # no total sem furar o escopo de quem é limitado a equipes.
    teams = user.sales_teams or []
    unscoped = user.role == UserRole.ADMIN or not teams
    if team is None and unscoped:
        orphan_stmt = (
            select(
                agg_cte.c.bling_store_id.label("bling_store_id"),
                agg_cte.c.pedidos.label("pedidos"),
                agg_cte.c.faturamento.label("faturamento"),
            )
            .select_from(agg_cte)
            .outerjoin(
                StoreInfo,
                StoreInfo.bling_store_id == agg_cte.c.bling_store_id,
            )
            .where(StoreInfo.id.is_(None))
        )
        for r in (await session.execute(orphan_stmt)).all():
            ped = int(r.pedidos or 0)
            fat = float(r.faturamento or 0)
            itens.append(FaturamentoLinha(
                store_id=(
                    str(r.bling_store_id)
                    if r.bling_store_id is not None
                    else "sem-loja"
                ),
                loja=(
                    f"Sem cadastro ({r.bling_store_id})"
                    if r.bling_store_id is not None
                    else "Sem cadastro"
                ),
                tipo=None,
                pedidos=ped,
                faturamento=round(fat, 2),
                ticket_medio=round(fat / ped, 2) if ped else 0.0,
            ))
            total_pedidos += ped
            total_faturamento += fat

    # Maior faturamento primeiro; lojas zeradas caem pro fim, ordenadas por
    # nome pra a lista ficar estável.
    itens.sort(key=lambda x: (-x.faturamento, (x.loja or "").lower()))

    return FaturamentoOut(
        itens=itens,
        total_pedidos=total_pedidos,
        total_faturamento=round(total_faturamento, 2),
        start=start,
        end=end,
        teams=list(teams_avail),
        team=team,
    )
