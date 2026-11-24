"""Aba Faturamento — relatório por loja de pedidos faturáveis.

Fonte LOCAL: davinci.bling_orders. Não chama API do Bling.

Definições fixadas com o operador:
- Conta situacao em {'6' (Em aberto), '15' (Em andamento), '83953' (Entregue)}.
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
- O filtro "Equipe" é a equipe de VENDAS (store_info.sales_team), com nome:
  101 Londres, 102 Israel, 103 Marrocos (30/10/2026). A comercial
  (commercial_team) segue no Valuation, não aqui.
- Período: default últimos 90 dias.
- Filtro "Tipo" (só admin; 17/11/2026, Novos procedimentos 7): Celular, Mala,
  Eletro ou Sem categoria É O QUE CADA LOJA VENDEU DAQUILO, não mais o
  faturamento inteiro das lojas classificadas no tipo (61 das 91 lojas são
  Celular+Eletro, e "Celular" e "Eletro" davam o mesmo número). O total do
  pedido (o mesmo max(total) do dedup) se divide entre os itens na proporção
  de itemvalor × quantidade (soma 0 → partes iguais), o rateio do Valuation;
  cada item vai para o departamento do PRODUTO (services/departamento.py,
  `departamento_do_item`: segmento raiz, categorias do Bling, família do SKU).
  A loja mostra só a sua parte e some se não vendeu o departamento; loja sem
  tipo, presencial e órfã entram quando venderam. Os 4 somam o total sem
  filtro. Sem filtro, nada muda.
- M-22 (24/11/2026, prévia aprovada em 23/11): cada linha traz também a
  `equipe` (sales_team), as `contas_preco` da loja (a tela conta os cartões
  vermelhos delas na grade: Pendências), o `investimento` do período (gasto
  de Ads guardado por dia em marketing_gasto_dia; None = sem dado) e, SÓ para
  administrador, a `margem` (menor e maior margin1..5 das contas de preço da
  loja) — a quem não é ADM a resposta sai SEM o campo. Como cada conta se liga
  à loja: services/faturamento_contas.py. As regras de quais pedidos entram e
  de quem vê quais lojas não mudaram.
- M-36 (24/11/2026, prévia aprovada em 24/11): o `investimento` passou a ser
  afiliados + Ads do Marketing, com os pedaços ao lado (`investimento_
  afiliados`, `investimento_ads` — a Margem da M-37 usa só o de Ads): o Ads
  por dia de marketing_gasto_dia quando a loja tem (ML), senão as coletas
  semanais da Conferência (Shopee: afiliados + Ads), ligadas à loja pela chave
  interna da conta. `investimento_ate`: até onde vão as semanas fechadas da
  Conferência quando param antes do fim do período. Regras em
  services/faturamento_contas.py e services/faturamento_conferencia.py.
- M-37 (24/11/2026, prévia aprovada em 24/11): a `margem` (só ADM) deixou de
  ser a faixa da aba Contas e virou a margem real da Conciliação: (Σ sobrou de
  todos os pedidos − Ads) ÷ Σ custo, pela data do pedido, nos dias do filtro.
  Cada pedido vai para a loja do Bling dele (bling_id) — a órfã "Sem
  cadastro" também ganha a dela —, e o Ads é o pedaço de Ads do Investimento
  acima (sem reler o Marketing). Regras em services/faturamento_margem.py.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import Text, case, func, literal, select
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_permission, user_scope
from app.deps.team_scope import nome_da_equipe
from app.models import BlingOrder, StoreInfo, User, UserRole
from app.schemas.faturamento import FaturamentoLinha, FaturamentoOut, MargemReal
from app.services import departamento as dep_svc
from app.services import faturamento_contas as contas_svc
from app.services import faturamento_margem as margem_svc
from app.services.store_departments import resolve_store_departments

router = APIRouter(prefix="/api/faturamento", tags=["faturamento"])


class EquipeFaturamento(BaseModel):
    """Uma opção do filtro "Equipe": o número de store_info.sales_team e o nome."""

    numero: int
    nome: str


class FaturamentoEquipesOut(FaturamentoOut):
    # 30/10/2026: as equipes com nome (Londres/Israel/Marrocos) AO LADO de
    # `teams`, que segue só com os números — quem lê a lista antiga não quebra.
    equipes: list[EquipeFaturamento] = []


# Bling situação IDs que contam pro faturamento da empresa:
#   '6'     → Em aberto
#   '15'    → Em andamento
#   '83953' → Entregue
# Demais status (etiqueta gerada, em rota, devolução, cancelado…) são ignorados.
_SITUACOES_FATURAVEIS = ("6", "15", "83953")


async def _agg_do_departamento(session: AsyncSession, department: str, filtros: tuple):
    """CTE `agg` (bling_store_id, pedidos, faturamento) só com a parte de
    `department` (17/11/2026). Uma consulta agregada: o departamento de cada
    item sai do mapa calculado uma vez (dep_svc.mapa_dos_itens) e entra como a
    lista das chaves (SKU + categoria) daquele departamento.

    Por pedido (o mesmo dedup bling_id + loja do total): parte = max(total) ×
    peso dos itens do departamento ÷ peso de todos, com peso = itemvalor ×
    quantidade; peso total 0 → pelo número de itens. Pedidos = os que têm
    item do departamento; só entra loja com algum."""
    idx = await dep_svc.indice_departamentos(session)
    mapa = await dep_svc.mapa_dos_itens(session, idx, *filtros)
    chaves = sorted(chave for chave, dep in mapa.items() if dep == department)
    # IN (SELECT unnest(...)) e não = ANY(...): o Postgres faz um hash das
    # chaves uma vez (o ANY de um parâmetro compara linha a linha com a lista
    # toda — num período longo são milhares de chaves × dezenas de mil itens).
    do_dep = case(
        (dep_svc.chave_do_item().in_(select(func.unnest(literal(chaves, ARRAY(Text))))), 1),
        else_=0,
    )
    itens = (
        select(
            BlingOrder.bling_id.label("bling_id"),
            BlingOrder.loja.label("bling_store_id"),
            BlingOrder.total.label("total"),
            (
                func.coalesce(BlingOrder.itemvalor, 0)
                * func.coalesce(BlingOrder.item_quantidade, 1)
            ).label("peso"),
            do_dep.label("do_dep"),
        )
        .where(*filtros)
        .cte("itens")
    )
    ped = (
        select(
            itens.c.bling_store_id.label("bling_store_id"),
            func.max(itens.c.total).label("total"),
            func.sum(itens.c.peso).label("peso"),
            func.sum(itens.c.peso * itens.c.do_dep).label("peso_dep"),
            func.count().label("itens"),
            func.sum(itens.c.do_dep).label("itens_dep"),
        )
        .group_by(itens.c.bling_id, itens.c.bling_store_id)
        .cte("ped")
    )
    parte = case(
        (ped.c.peso != 0, ped.c.total * ped.c.peso_dep / ped.c.peso),
        else_=ped.c.total * ped.c.itens_dep / ped.c.itens,
    )
    return (
        select(
            ped.c.bling_store_id.label("bling_store_id"),
            func.count().label("pedidos"),
            func.coalesce(func.round(func.sum(parte), 2), 0).label("faturamento"),
        )
        .where(ped.c.itens_dep > 0)
        .group_by(ped.c.bling_store_id)
        .cte("agg")
    )


def _dias_do_periodo(start: datetime, end: datetime) -> tuple[date, date]:
    """Os dias do período para o Investimento: o fim é exclusivo (o mês vem
    como [dia 1, dia 1 do mês seguinte); o personalizado até 23:59:59)."""
    return start.date(), (end - timedelta(microseconds=1)).date()


def _reais(v: Decimal | None) -> float | None:
    return None if v is None else float(round(v, 2))


def _margem_real(
    pedidos: margem_svc.PedidosDaLoja, inv: contas_svc.Investimento | None, inicio: date
) -> MargemReal:
    """M-37: a Margem da linha. Ads = SÓ o pedaço de Ads do Investimento da
    M-36 (sem dado = 0); os afiliados já saem do sobrou de cada pedido."""
    m = margem_svc.margem(pedidos, inv.ads if inv is not None else None, inicio)
    return MargemReal(
        valor=None if m.valor is None else float(m.valor),
        sobrou=float(round(m.sobrou, 2)),
        ads=float(round(m.ads, 2)),
        ads_com_dado=m.ads_com_dado,
        ads_ate=inv.ate if inv is not None and m.ads_com_dado else None,
        custo=float(round(m.custo, 2)),
        pedidos=pedidos.pedidos,
        pagos=pedidos.pagos,
        previstos=pedidos.previstos,
        cancelados=pedidos.cancelados,
        sem_valor=pedidos.sem_valor,
        inicio=pedidos.inicio,
        antes_do_inicio=m.antes_do_inicio,
    )


async def _colunas_m22(
    session: AsyncSession,
    itens: list[FaturamentoLinha],
    equipe_da_loja: dict[str, int | None],
    bling_da_loja: dict[str, str | None],
    start: datetime,
    end: datetime,
    *,
    admin: bool,
) -> None:
    """Preenche as colunas da M-22 nas linhas de loja cadastrada (a órfã fica
    sem conta e sem investimento). A Margem (M-37, só ADM) vem da Conciliação
    pela loja do Bling, então a órfã "Sem cadastro (id)" também tem a dela."""
    integ_por_loja = await contas_svc.integracoes_das_lojas(session)
    contas_preco = await contas_svc.contas_preco_das_lojas(session, integ_por_loja)
    contas_ads = await contas_svc.contas_ads_das_lojas(session, integ_por_loja)
    contas_conferencia = await contas_svc.contas_conferencia_das_lojas(session, integ_por_loja)
    inicio, fim = _dias_do_periodo(start, end)
    investimentos = await contas_svc.investimento_das_lojas(
        session, contas_ads, inicio, fim, contas_conferencia
    )
    pedidos_conciliacao: dict[str, margem_svc.PedidosDaLoja] = {}
    if admin:
        # A loja do Bling de cada linha: a cadastrada pelo bling_store_id; a
        # órfã é o próprio id ("sem-loja" = pedido sem loja: nada a ligar).
        bling_das_linhas = {
            it.store_id: (
                bling_da_loja.get(it.store_id)
                if it.store_id in equipe_da_loja
                else (it.store_id if it.store_id != "sem-loja" else None)
            )
            for it in itens
        }
        pedidos_conciliacao = await margem_svc.pedidos_das_lojas(
            session,
            inicio,
            fim,
            {k: str(v).strip() for k, v in bling_das_linhas.items() if v and str(v).strip()},
            integ_por_loja,
        )
    for it in itens:
        cadastrada = it.store_id in equipe_da_loja
        inv = investimentos.get(UUID(it.store_id)) if cadastrada else None
        pedidos = pedidos_conciliacao.get(it.store_id)
        if admin and pedidos is not None:
            it.margem = _margem_real(pedidos, inv, inicio)
        if not cadastrada:
            continue  # órfã ("Sem cadastro"): sem conta nem investimento
        loja = UUID(it.store_id)
        it.equipe = equipe_da_loja[it.store_id]
        contas = contas_preco.get(loja, [])
        it.contas_preco = sorted(str(c.id) for c in contas)
        if inv is not None and inv.total is not None:
            it.investimento = float(round(inv.total, 2))
            it.investimento_afiliados = _reais(inv.afiliados)
            it.investimento_ads = _reais(inv.ads)
            it.investimento_desde = inv.desde
            it.investimento_parcial = inv.parcial
            it.investimento_ate = inv.ate


@router.get("", response_model=FaturamentoEquipesOut)
async def list_faturamento(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("faturamento", "view"))],
    start: datetime | None = Query(None),
    end: datetime | None = Query(None),
    team: int | None = Query(None),
    department: str | None = Query(None),
) -> FaturamentoEquipesOut:
    """Resumo por loja, no período, dos pedidos entregues.

    Parte do roster de lojas (store_info) — toda loja do escopo aparece,
    zerada se não vendeu — e faz LEFT JOIN nos pedidos agregados. Pedido de
    loja sem cadastro entra como "Sem cadastro" só na visão sem escopo.
    user_scope(StoreInfo, user) + `team` aplicam o filtro de equipe.
    `department` (só admin: celular, mala, eletro ou sem) troca o total de
    cada loja pela parte dos itens daquele departamento e tira as lojas que
    não venderam nada dele (_agg_do_departamento).
    """
    if department is not None:
        if user.role != UserRole.ADMIN:
            raise HTTPException(403, detail={"code": "department_not_allowed"})
        # 17/11/2026: os departamentos do produto (celular, mala, eletro e
        # "sem" = Sem categoria); outra raiz de segmento (shein, catalogo…)
        # não é mais filtro.
        if department not in dep_svc.DEPARTAMENTOS:
            raise HTTPException(400, detail={"code": "invalid_department"})

    if end is None:
        end = datetime.now(UTC)
    if start is None:
        start = end - timedelta(days=90)

    # Equipes de VENDAS entre as lojas que o usuário já pode consultar
    # (30/10/2026: era a comercial, que é 1 em toda loja e escondia o
    # filtro). Admin vê as três; quem é de equipe, só a(s) dele.
    teams_avail = (
        await session.execute(
            select(StoreInfo.sales_team)
            .where(StoreInfo.sales_team.isnot(None), user_scope(StoreInfo, user))
            .distinct()
            .order_by(StoreInfo.sales_team)
        )
    ).scalars().all()

    # Filtro explícito de equipe: só vale se o usuário tem acesso a ela.
    if team is not None and team not in teams_avail:
        raise HTTPException(status_code=403, detail={"code": "team_not_allowed"})

    # user_scope já restringe ao escopo do usuário; `team` afunila mais.
    scope_clauses = [user_scope(StoreInfo, user)]
    if team is not None:
        scope_clauses.append(StoreInfo.sales_team == team)

    # A classificação vem da mesma regra de Lojas, carregada em lote — hoje
    # só a etiqueta da coluna "Tipo"; o filtro de departamento é pelos itens
    # vendidos (ETAPA 1), não afunila mais o roster.
    stores = (
        await session.execute(select(StoreInfo).where(*scope_clauses))
    ).scalars().all()
    departments_by_store = await resolve_store_departments(session, user, stores)
    # Keep the roster consistent with the batch just classified even when
    # another request creates a store between these two queries.
    scope_clauses.append(StoreInfo.id.in_(list(departments_by_store)))

    filtros_pedido = (
        BlingOrder.situacao.in_(_SITUACOES_FATURAVEIS),
        BlingOrder.data >= start,
        BlingOrder.data < end,
    )
    if department is None:
        # ETAPA 1 — pedidos do período: dedup por bling_id (bling_orders tem
        # uma linha por item, `total` repete) e agrega por loja Bling (pedidos
        # + faturamento). `agg` fica com UMA linha por bling_store_id.
        ord_cte = (
            select(
                BlingOrder.bling_id.label("bling_id"),
                BlingOrder.loja.label("bling_store_id"),
                func.max(BlingOrder.total).label("total"),
            )
            .where(*filtros_pedido)
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
    else:
        # ETAPA 1 com filtro — a mesma, só com a parte do departamento.
        agg_cte = await _agg_do_departamento(session, department, filtros_pedido)

    # ETAPA 2 — roster de lojas do escopo. PARTE de store_info (não dos
    # pedidos) pra TODA loja aparecer, mesmo zerada. LEFT JOIN nos pedidos
    # agregados; loja sem venda fica com pedidos/faturamento = 0. Chave =
    # store_info.id (UUID), única até p/ as ~11 lojas sem bling_store_id.
    # bling_store_id é único em store_info, então o join não faz fan-out.
    # Com filtro de departamento, só a loja que vendeu aquilo (INNER JOIN).
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
        .join(
            agg_cte,
            agg_cte.c.bling_store_id == StoreInfo.bling_store_id,
            isouter=department is None,
        )
        .where(*scope_clauses)
    )

    itens: list[FaturamentoLinha] = []
    total_pedidos = 0
    total_faturamento = 0.0
    equipe_da_loja = {str(s.id): s.sales_team for s in stores}
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
            departments=departments_by_store[r.store_uuid].departments,
            pedidos=ped,
            faturamento=round(fat, 2),
            ticket_medio=round(fat / ped, 2) if ped else 0.0,
        ))
        total_pedidos += ped
        total_faturamento += fat

    # ETAPA 3 — pedidos órfãos (loja sem cadastro em store_info). Só na visão
    # SEM escopo (admin/usuário sem equipe) e SEM filtro de equipe: órfão não
    # tem equipe. Mantém a receita órfã no total sem furar o escopo de quem é
    # limitado a equipes. Com filtro de departamento (17/11/2026) o órfão
    # entra com a parte dele, se vendeu aquilo.
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

    admin = user.role == UserRole.ADMIN
    bling_da_loja = {str(s.id): s.bling_store_id for s in stores}
    await _colunas_m22(session, itens, equipe_da_loja, bling_da_loja, start, end, admin=admin)

    out = FaturamentoEquipesOut(
        itens=itens,
        total_pedidos=total_pedidos,
        total_faturamento=round(total_faturamento, 2),
        start=start,
        end=end,
        teams=list(teams_avail),
        equipes=[EquipeFaturamento(numero=n, nome=nome_da_equipe(n)) for n in teams_avail],
        team=team,
    )
    if admin:
        return out
    # A Margem é só do administrador (M-22; continua na M-37): para os outros o
    # campo nem vai na resposta (o mesmo critério de ADM da Tabela de Preços:
    # role == admin) e a Conciliação nem é lida.
    return JSONResponse(
        out.model_dump(mode="json", exclude={"itens": {"__all__": {"margem"}}})
    )
