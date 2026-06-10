"""Controle de Estoque router.

3 tabs on the front-end, each backed by one GET here:
  * /api/estoque/produtos    — Estoque tab: per-SKU summary of the day's
                               movements + current stock + reserve.
  * /api/estoque/pedidos     — Pedidos tab: bling_orders flagged as
                               "enviado etiqueta" (situacao=15), filtered
                               by the operator's stock_tag.
  * /api/estoque/envios      — Envios tab: per-day shipment count for
                               orders whose `em_andamento_data` falls
                               inside the window.
  * POST /api/estoque/check  — upserts the conferido checkbox for any
                               of the three sections.
  * POST /api/estoque/movement/{id}/obs — operator inline-edit of the
                               movement observação (webhook doesn't
                               carry it; this is the manual hook).

Scoping:
  * Admin (UserRole.ADMIN) sees everything; can pass `?tag=` to
    narrow to a specific operator's view.
  * Operator (stock_tag set, role != admin) sees ONLY products /
    orders whose SKU ends with `.{stock_tag}` and `situacao = 'A'`,
    `formato = 'S'` (simples — kits aren't operated by warehouse).
  * Anyone without role=admin AND without stock_tag → 403.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Any
from uuid import UUID
from zoneinfo import ZoneInfo

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import Date, and_, cast, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_permission
from app.models import BlingOrder, EstoqueDiaFinalizado, Product, User, UserRole
from app.models.company import Store
from app.models.integration import Integration
from app.models.stock_check import StockCheck
from app.models.stock_movement import StockMovement
from app.services.sku_tags import VALID_TAGS as _VALID_TAGS
from app.services.sku_tags import sql_clause_for_tag as _sql_clause_for_tag

logger = structlog.get_logger()
router = APIRouter(prefix="/api/estoque", tags=["estoque"])

# Operator is in Brazil — "hoje" no contexto da aba Controle de Estoque
# significa hoje em São Paulo, não em UTC. Sem isso, das 21h às 24h locais
# (00h–03h UTC do dia seguinte) o filtro de data mostra o dia errado:
# pedidos lançados em 26/05 (BRT) caíam em 27/05 (UTC) e sumiam quando o
# operador seleciona "26/05" no date-picker. Mesmo padrão usado em
# bling_orders.py + marketplace_shipment_check.py + worker.py.
_BRT = ZoneInfo("America/Sao_Paulo")

# Classificação SKU → tag (constantes + clause SQL) vive em
# app.services.sku_tags, fonte única compartilhada com Devoluções —
# importada acima como _VALID_TAGS / _sql_clause_for_tag.

# Universal junk-SKU exclusions — applied to every produtos/sync-stocks
# view regardless of tag. These SKUs (numeric labels 1-30, `n9`
# placeholder) are caixa/embalagem identifiers, never real products the
# operator needs to track. They were leaking into the admin "todas"
# view because the mala-specific filter only runs when tag=mala.
_BASELINE_NUMERIC_SKUS = tuple(str(n) for n in range(1, 31))

# Bling situação ID for "enviado etiqueta" — confirmed against prod
# distinct values: id=15 has 735/928 rows with em_andamento_data set,
# the highest correspondence rate of any situação. id=12 is cancelado;
# 83953/83957 are custom statuses for this shop.
# Situação custom do shop para "Enviado Etiqueta" (etiqueta gerada,
# esperando marketplace confirmar envio).
_SITUACAO_ENVIADO_ETIQUETA = "83965"
# Situação default Bling para "Atendido" (marketplace confirmou,
# em_andamento_data deveria estar preenchida).
_SITUACAO_ATENDIDO = "15"
# Situação pós-15: pedido já entregue ao cliente. Ainda deve aparecer na aba
# (mesmo dia, badge verde) — operacionalmente é "enviado/entregue", não some.
_SITUACAO_ENTREGUE = "83953"
# Cliente pediu devolução — pedido continua "do dia" em que foi enviado,
# permanece visível na aba (badge verde). A devolução em si é tratada em
# outro fluxo; a aba de Pedidos não esconde o histórico de envio.
_SITUACAO_AGUARDANDO_DEVOLUCAO = "83957"
# Caso de devolução já tratado pelo time — mesmo critério: pedido foi
# enviado no dia, fica visível.
_SITUACAO_RESOLVIDO = "545902"
# Pedidos pendentes mais antigos que isso = zumbis (webhook perdido) —
# ficam escondidos da aba.
_PENDENTE_MAX_AGE_DIAS = 14


def _baseline_sku_exclusions(column):
    """Universal exclusions applied to every produtos/sync-stocks view.
    Returns a list of clauses to extend the WHERE with. Keep narrow —
    only patterns that are NEVER tracked products under ANY tag belong
    here (numeric labels, `n9`)."""
    return [
        column.op("!~")("^[0-9]+$"),
        column.notin_(_BASELINE_NUMERIC_SKUS),
        func.lower(column) != "n9",
    ]


def _user_sees_all_checks(user: User) -> bool:
    """True se o user pode ver conferências (StockCheck) agregadas de
    TODOS os usuários — admins por padrão, e qualquer user com a flag
    permissions['controle_estoque_see_all']=true (papel de gerente).

    Esta função controla SÓ a visualização agregada (bool_or em
    StockCheck). NÃO concede outros poderes de admin (tag override em
    _resolve_tags, ticar section='envio' em toggle_estoque_check) —
    esses continuam gated por user.role == UserRole.ADMIN."""
    if user.role == UserRole.ADMIN:
        return True
    perms = user.permissions or {}
    return bool(perms.get("controle_estoque_see_all", False))


def _resolve_tags(user: User, override: str | None) -> list[str] | None:
    """Returns the list of tags to OR-filter products by. `None` means
    "no tag filter" (admin viewing all). Empty list also collapses to
    None — UI sends "" for "todas" no dropdown.

    Admin: honra `override` se vier; senão None (vê tudo).
    Non-admin: se vier `override` E estiver entre as stock_tags do user,
    restringe ao override (sub-seleção — ex. churchill tem todas as 9 tags
    e filtra uma por vez pela UI). Senão, devolve todas as stock_tags.
    Segurança preservada: usuário não consegue ver tag fora do seu set."""
    if user.role == UserRole.ADMIN:
        if override:
            ov = override.strip().lower()
            if ov not in _VALID_TAGS:
                raise HTTPException(400, detail={"code": "invalid_tag"})
            return [ov]
        return None

    allowed = [
        t.lower() for t in (user.stock_tags or [])
        if isinstance(t, str) and t.lower() in _VALID_TAGS
    ]
    if not allowed:
        raise HTTPException(403, detail={"code": "no_stock_tag"})

    if override:
        ov = override.strip().lower()
        if ov not in _VALID_TAGS:
            raise HTTPException(400, detail={"code": "invalid_tag"})
        if ov not in allowed:
            raise HTTPException(403, detail={"code": "tag_not_allowed"})
        return [ov]

    return allowed


def _resolve_dates(
    data_inicio: date | None, data_fim: date | None
) -> tuple[date, date]:
    """Both default to today. Caller already received `date` objects."""
    today = datetime.now(_BRT).date()
    return (data_inicio or today, data_fim or today)


# ─── SEÇÃO 1: ESTOQUE ────────────────────────────────────────────────────


def _stock_filter_clause(estoque_filter: str | None):
    """Returns a SQLAlchemy clause against Product.stock for the
    'com' / 'sem' modes. None = no filter (the 'all'/default mode).

    "com estoque" = Product.stock > 0 (virtual; what's available to sell)
    "sem estoque" = Product.stock == 0 OR NULL (out of stock OR never synced)
    """
    if estoque_filter == "com":
        return Product.stock > 0
    if estoque_filter == "sem":
        return or_(Product.stock == 0, Product.stock.is_(None))
    return None  # 'all' or unset


@router.get("/produtos")
async def list_estoque_produtos(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("controle_estoque", "view"))],
    data_inicio: date | None = Query(None),
    data_fim: date | None = Query(None),
    tag: str | None = Query(None),  # admin-only override
    estoque_filter: str | None = Query(None, pattern="^(all|com|sem)$"),
) -> dict[str, Any]:
    tags = _resolve_tags(user, tag)
    data_inicio, data_fim = _resolve_dates(data_inicio, data_fim)
    window_start = datetime.combine(data_inicio, time.min, tzinfo=UTC)
    window_end = datetime.combine(data_fim, time.max, tzinfo=UTC)

    # `formato='S'` should already exclude kits, but prod data has some
    # compound SKUs ("x009.ci+a001.ci") sneaking through with the
    # simples flag set incorrectly. Belt-and-suspenders: drop anything
    # whose SKU contains a '+' character regardless of `formato`.
    where: list = [
        Product.situacao == "A",
        Product.formato == "S",
        Product.sku.notlike("%+%"),
        *_baseline_sku_exclusions(Product.sku),
    ]
    if tags is not None:
        # OR across each tag's pattern — operator with [ci, ra] sees
        # products ending in .ci OR .ra.
        where.append(or_(*[_sql_clause_for_tag(Product.sku, t) for t in tags]))
    stock_clause = _stock_filter_clause(estoque_filter)
    if stock_clause is not None:
        where.append(stock_clause)

    products = (
        await session.execute(
            select(
                Product.id, Product.sku, Product.name,
                Product.stock, Product.reserved_stock,
            )
            .where(and_(*where))
            .order_by(Product.sku)
        )
    ).all()
    if not products:
        return {
            "data": [],
            "periodo": {"inicio": str(data_inicio), "fim": str(data_fim)},
        }

    skus = [p.sku for p in products if p.sku]
    movements = (
        await session.execute(
            select(
                StockMovement.id,
                StockMovement.sku,
                StockMovement.tipo,
                StockMovement.quantidade,
                StockMovement.observacao,
                StockMovement.origem,
                StockMovement.date,
            )
            .where(
                and_(
                    StockMovement.sku.in_(skus),
                    StockMovement.date >= window_start,
                    StockMovement.date <= window_end,
                )
            )
            .order_by(StockMovement.date.asc())
        )
    ).all()

    # Per-SKU buckets of individual entradas and saídas. We expose the
    # full lists so the front-end can render one "{qty} - {obs}" row per
    # entrada (matches what the operator sees in the Bling planilha).
    # `saida_qty_total` is a convenience sum — operators still want the
    # day's total for the saída column. `saida_origens` is the
    # comma-list of pedido numbers.
    by_sku: dict[str, dict[str, Any]] = {}
    for m in movements:
        slot = by_sku.setdefault(
            m.sku,
            {"entradas": [], "saidas": [], "saida_qty_total": 0, "saida_origens": []},
        )
        if m.tipo == "E":
            slot["entradas"].append({
                "movement_id": str(m.id),
                "qty": int(m.quantidade or 0),
                "obs": m.observacao or "",
            })
        elif m.tipo == "S":
            slot["saidas"].append({
                "movement_id": str(m.id),
                "qty": int(m.quantidade or 0),
                "origem": m.origem or "",
            })
            slot["saida_qty_total"] += int(m.quantidade or 0)
            if m.origem:
                slot["saida_origens"].append(m.origem)

    check_where = [
        StockCheck.section == "estoque",
        StockCheck.reference_date >= data_inicio,
        StockCheck.reference_date <= data_fim,
    ]
    if _user_sees_all_checks(user):
        # Admin: dia conferido se QUALQUER usuário marcou conferido.
        checks_rows = (await session.execute(
            select(
                StockCheck.reference_id,
                func.bool_or(StockCheck.conferido).label("any_conf"),
            )
            .where(and_(*check_where))
            .group_by(StockCheck.reference_id)
        )).all()
        checks = {r.reference_id: bool(r.any_conf) for r in checks_rows}
    else:
        check_where.append(StockCheck.user_id == user.id)
        checks_rows = (await session.execute(
            select(StockCheck.reference_id, StockCheck.conferido).where(and_(*check_where))
        )).all()
        checks = {r.reference_id: bool(r.conferido) for r in checks_rows}

    result: list[dict[str, Any]] = []
    for p in products:
        slot = by_sku.get(p.sku, {})
        virtual = int(p.stock or 0)
        reserved = int(p.reserved_stock or 0)
        # `Product.stock` is the VIRTUAL balance (Bling saldoVirtualTotal).
        # The operator's "saldo atual" column wants the FÍSICO total —
        # virtual + reserved reconstructs that. No new column needed.
        saldo_fisico = virtual + reserved
        result.append({
            "sku": p.sku,
            "nome": p.name,
            "entradas": slot.get("entradas") or [],
            "saidas": slot.get("saidas") or [],
            "saida_qty_total": int(slot.get("saida_qty_total", 0)),
            "saida_origens": ", ".join(slot.get("saida_origens") or []),
            "saldo_fisico": saldo_fisico,
            "saldo_virtual": virtual,
            "reserva": reserved,
            "conferido": checks.get(p.sku, False),
        })

    return {
        "data": result,
        "periodo": {"inicio": str(data_inicio), "fim": str(data_fim)},
    }


# ─── SEÇÃO 2: PEDIDOS ────────────────────────────────────────────────────


@router.get("/pedidos")
async def list_estoque_pedidos(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("controle_estoque", "view"))],
    data_inicio: date | None = Query(None),
    data_fim: date | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),  # enviado | nao_enviado
    tag: str | None = Query(None),
) -> dict[str, Any]:
    """Lista pedidos da aba via 2 caminhos paralelos:
      * PENDENTE: situacao=83965 (Enviado Etiqueta) + sem em_andamento_data
        + criado nos últimos 14 dias (anti-zumbi) → badge vermelho.
      * ENVIADO:  situacao IN (83965, 15, 83953, 83957, 545902) +
        em_andamento_data preenchida → badge verde (exceto 83965, que fica
        vermelho mesmo com data). 83957=Aguardando Devolução e 545902=
        Resolvido continuam visíveis: pedido já saiu do estoque, fluxo de
        devolução é tratado em outra aba.
    Qualquer outra combinação fica escondida: `15` sem em_andamento_data
    é anomalia (já atendido, não é "não enviado"); 83965 com >14 dias é
    zumbi de webhook perdido; outras situações custom não pertencem ao
    fluxo. Data de referência: ship date se confirmado, senão HOJE (BRT)
    — pendente "fixa" no dia atual e aparece todo dia até confirmar."""
    tags = _resolve_tags(user, tag)
    data_inicio, data_fim = _resolve_dates(data_inicio, data_fim)

    # HOJE em BRT — usado tanto no corte anti-zumbi quanto no "pin" da
    # data efetiva dos pendentes. BRT explícito (não func.now()::date,
    # que daria a data UTC e divergiria do filtro à noite).
    today_brt = datetime.now(_BRT).date()

    pendente_clause = and_(
        BlingOrder.situacao == _SITUACAO_ENVIADO_ETIQUETA,
        BlingOrder.em_andamento_data.is_(None),
        cast(BlingOrder.data, Date) >= today_brt - timedelta(days=_PENDENTE_MAX_AGE_DIAS),
    )
    enviado_clause = and_(
        BlingOrder.situacao.in_(
            (
                _SITUACAO_ENVIADO_ETIQUETA,
                _SITUACAO_ATENDIDO,
                _SITUACAO_ENTREGUE,
                _SITUACAO_AGUARDANDO_DEVOLUCAO,
                _SITUACAO_RESOLVIDO,
            )
        ),
        BlingOrder.em_andamento_data.isnot(None),
    )
    where: list = [or_(pendente_clause, enviado_clause)]
    if tags is not None:
        # Same OR-pattern as the produtos endpoint, but applied to
        # BlingOrder.item_codigo since pedidos are filtered by the
        # ordered item's SKU.
        where.append(or_(*[_sql_clause_for_tag(BlingOrder.item_codigo, t) for t in tags]))

    # Data de referência: ship date se confirmado, senão HOJE (BRT).
    effective_date = func.coalesce(BlingOrder.em_andamento_data, today_brt)
    where.append(effective_date >= data_inicio)
    where.append(effective_date <= data_fim)

    # Filtros por status — alinhado com o `status` do payload (que é por
    # situacao, não por em_andamento_data). `nao_enviado` = badge vermelho =
    # situacao=83965 (Enviado Etiqueta — agência ainda não confirmou),
    # INDEPENDENTE de ter ou não em_andamento_data carimbada (o sync de
    # etiqueta já carimba a data provisória). Filtrar por em_andamento_data
    # IS NULL aqui escondia os 83965 com data — operador via 108 no
    # contador mas tabela vazia no filtro "não enviado".
    if status_filter == "nao_enviado":
        where.append(BlingOrder.situacao == _SITUACAO_ENVIADO_ETIQUETA)
        order_by = effective_date.desc()
    elif status_filter == "enviado":
        where.append(BlingOrder.situacao.in_(
            (
                _SITUACAO_ATENDIDO,
                _SITUACAO_ENTREGUE,
                _SITUACAO_AGUARDANDO_DEVOLUCAO,
                _SITUACAO_RESOLVIDO,
            )
        ))
        order_by = BlingOrder.em_andamento_data.desc()
    else:
        # status='todos' ou None → mostra ambos, sort por effective_date.
        order_by = effective_date.desc()

    orders = (
        await session.execute(
            select(BlingOrder).where(and_(*where)).order_by(order_by)
        )
    ).scalars().all()

    # Build a bling_store_id → "Plataforma loja" map for every store
    # referenced by the result set. One query, no N+1. Falls back to the
    # raw ID string when the lookup misses (rare — manual data, etc).
    store_ids: set[int] = set()
    for o in orders:
        try:
            store_ids.add(int(o.loja))
        except (TypeError, ValueError):
            continue
    store_name_by_id: dict[int, str] = {}
    if store_ids:
        rows = (
            await session.execute(
                select(Store.bling_store_id, Integration.name, Integration.platform)
                .join(Integration, Integration.id == Store.integration_id, isouter=True)
                .where(Store.bling_store_id.in_(store_ids))
            )
        ).all()
        for r in rows:
            try:
                bsid = int(r.bling_store_id)
            except (TypeError, ValueError):
                continue
            plat = (r.platform.value if hasattr(r.platform, "value") else str(r.platform or "")).strip()
            label = (r.name or "").strip()
            if plat and label:
                store_name_by_id[bsid] = f"{plat.upper()} {label}"
            elif label:
                store_name_by_id[bsid] = label

    order_ids = [str(o.id) for o in orders]
    checks_map: dict[str, dict[str, Any]] = {}
    if order_ids:
        check_where = [
            StockCheck.section == "pedido",
            StockCheck.reference_id.in_(order_ids),
        ]
        if _user_sees_all_checks(user):
            # Admin: conferido se QUALQUER usuário marcou; observações
            # de todos concatenadas.
            checks_rows = (await session.execute(
                select(
                    StockCheck.reference_id,
                    func.bool_or(StockCheck.conferido).label("any_conf"),
                    func.string_agg(StockCheck.observacao, " | ").label("observacao"),
                )
                .where(and_(*check_where))
                .group_by(StockCheck.reference_id)
            )).all()
            for r in checks_rows:
                checks_map[r.reference_id] = {
                    "conferido": bool(r.any_conf),
                    "observacao": r.observacao,
                }
        else:
            check_where.append(StockCheck.user_id == user.id)
            checks_rows = (await session.execute(
                select(StockCheck.reference_id, StockCheck.conferido, StockCheck.observacao)
                .where(and_(*check_where))
            )).all()
            for r in checks_rows:
                checks_map[r.reference_id] = {
                    "conferido": bool(r.conferido),
                    "observacao": r.observacao,
                }

    result: list[dict[str, Any]] = []
    for o in orders:
        check = checks_map.get(str(o.id), {"conferido": False, "observacao": None})
        bling_store_id: int | None = None
        try:
            bling_store_id = int(o.loja) if o.loja else None
        except (TypeError, ValueError):
            bling_store_id = None
        loja_name = (
            store_name_by_id.get(bling_store_id) if bling_store_id is not None else None
        ) or (o.loja or "")
        # Coluna "DATA ENVIO" na tela: SEMPRE a data de criação do pedido
        # (= quando o cliente comprou no marketplace). NÃO confundir com a
        # data em que o pedido aparece no filtro — isso é `effective_date`
        # na query (COALESCE(em_andamento_data, hoje)). `data_envio`
        # continua no JSON pra quem quiser o ship date confirmado.
        data_criacao = o.data.date() if o.data else None
        result.append({
            "id": str(o.id),
            "data": data_criacao.isoformat() if data_criacao else None,
            "data_pedido": o.data.isoformat() if o.data else None,
            "data_envio": o.em_andamento_data.isoformat() if o.em_andamento_data else None,
            "pedido_bling": o.numero,
            "pedido_marketplace": o.numeroloja,
            "loja": loja_name,
            "sku": o.item_codigo,
            "produto": o.item_descricao,
            "quantidade": o.item_quantidade or 1,
            # Badge por situacao (não por em_andamento_data):
            #   - 15 (Em andamento/Atendido), 83953 (Entregue), 83957
            #     (Aguardando Devolução), 545902 (Resolvido) → verde
            #     (pedido foi enviado, segue no histórico do dia mesmo se
            #     entrou em fluxo de devolução).
            #   - 83965 (Enviado Etiqueta) → vermelho (etiqueta gerada
            #     mas agência ainda não confirmou).
            "status": "enviado"
            if o.situacao in (
                _SITUACAO_ATENDIDO,
                _SITUACAO_ENTREGUE,
                _SITUACAO_AGUARDANDO_DEVOLUCAO,
                _SITUACAO_RESOLVIDO,
            )
            else "nao_enviado",
            "conferido": check["conferido"],
            "observacao": check["observacao"],
            "bling_id": o.bling_id,
        })

    # Atrasados (INDEPENDENTE do filtro de data): pedidos com etiqueta
    # gerada em dia PASSADO e ainda não confirmados pela agência
    # (situacao=83965 + em_andamento_data preenchida < hoje). O
    # effective_date desses é a própria em_andamento_data (passada), então
    # eles NÃO aparecem no filtro de hoje — por isso a contagem vem à
    # parte. O chip "atrasados" no topo da aba os mostra quando o operador
    # está no dia de hoje. Respeita o filtro de tag (mesma regra da lista).
    atr_where: list = [
        BlingOrder.situacao == _SITUACAO_ENVIADO_ETIQUETA,
        BlingOrder.em_andamento_data.isnot(None),
        BlingOrder.em_andamento_data < today_brt,
        BlingOrder.item_index == 0,
        BlingOrder.bling_id.isnot(None),
    ]
    if tags is not None:
        atr_where.append(or_(*[_sql_clause_for_tag(BlingOrder.item_codigo, t) for t in tags]))
    atrasados_rows = (await session.execute(
        select(
            BlingOrder.em_andamento_data,
            func.count(func.distinct(BlingOrder.bling_id)),
        )
        .where(and_(*atr_where))
        .group_by(BlingOrder.em_andamento_data)
    )).all()
    atrasados = sorted(
        ({"date": d.isoformat(), "count": int(n)} for d, n in atrasados_rows if d),
        key=lambda x: x["date"],
    )

    return {
        "data": result,
        "periodo": {"inicio": str(data_inicio), "fim": str(data_fim)},
        "atrasados": atrasados,
    }


# ─── SEÇÃO 3: ENVIOS ─────────────────────────────────────────────────────


@router.get("/envios")
async def list_estoque_envios(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("controle_estoque", "view"))],
    data_inicio: date | None = Query(None),
    data_fim: date | None = Query(None),
    tag: str | None = Query(None),
    conferido_filter: str | None = Query(
        None,
        alias="conferido",
        pattern="^(all|conferidos|nao_conferidos)$",
    ),
) -> dict[str, Any]:
    tags = _resolve_tags(user, tag)
    # Envios tab defaults to last 7 days when no window is set, matching
    # the page's date-picker default.
    today = datetime.now(_BRT).date()
    if data_inicio is None and data_fim is None:
        data_inicio = today - timedelta(days=6)
        data_fim = today
    else:
        data_inicio, data_fim = _resolve_dates(data_inicio, data_fim)

    where: list = [
        # Espelha o enviado_clause da aba Pedidos (linha ~363): só
        # pedidos efetivamente enviados (badge verde) contam como envios
        # físicos. Antes filtrava só por `em_andamento_data not null`,
        # incluindo 83965 (Etiqueta), 83955 (Aguardando Cancelamento),
        # 12 (Cancelado) — abas Pedidos vs Envios divergiam. Agora batem.
        BlingOrder.situacao.in_(
            (
                _SITUACAO_ATENDIDO,
                _SITUACAO_ENTREGUE,
                _SITUACAO_AGUARDANDO_DEVOLUCAO,
                _SITUACAO_RESOLVIDO,
            )
        ),
        BlingOrder.em_andamento_data.isnot(None),
        BlingOrder.em_andamento_data >= data_inicio,
        BlingOrder.em_andamento_data <= data_fim,
    ]
    if tags is not None:
        where.append(or_(*[_sql_clause_for_tag(BlingOrder.item_codigo, t) for t in tags]))

    rows = (
        await session.execute(
            select(
                BlingOrder.em_andamento_data.label("dia"),
                func.count(func.distinct(BlingOrder.bling_id)).label("envios"),
            )
            .where(and_(*where))
            .group_by(BlingOrder.em_andamento_data)
            .order_by(BlingOrder.em_andamento_data.desc())
        )
    ).all()

    check_where = [
        StockCheck.section == "envio",
        StockCheck.reference_date >= data_inicio,
        StockCheck.reference_date <= data_fim,
    ]
    if _user_sees_all_checks(user):
        # Admin: dia conferido se QUALQUER usuário marcou conferido.
        checks_rows = (await session.execute(
            select(
                StockCheck.reference_id,
                func.bool_or(StockCheck.conferido).label("any_conf"),
            )
            .where(and_(*check_where))
            .group_by(StockCheck.reference_id)
        )).all()
        checks = {r.reference_id: bool(r.any_conf) for r in checks_rows}
    else:
        check_where.append(StockCheck.user_id == user.id)
        checks_rows = (await session.execute(
            select(StockCheck.reference_id, StockCheck.conferido).where(and_(*check_where))
        )).all()
        checks = {r.reference_id: bool(r.conferido) for r in checks_rows}

    # Per-day stock conferência aggregate. Total de produtos é fixo
    # (não varia por dia); conferidos vem por reference_date a partir
    # da seção 'estoque'. O front exibe Total/Parcial/Não conferido
    # numa coluna nova ao lado de "Envios".
    total_produtos = await _count_active_products(session, tags)
    estoque_checks_by_day = await _count_estoque_checks_by_day(
        session, user_id=user.id, data_inicio=data_inicio, data_fim=data_fim,
        tags=tags, is_admin=_user_sees_all_checks(user),
    )
    # Dias travados como "total" (admin já tinha ticado CONFERIDO no
    # envio). Sem essa trava, o badge regredia pra "parcial" assim que
    # entrasse produto novo na tag. Migration 0134.
    locks = {row.data for row in (await session.execute(
        select(EstoqueDiaFinalizado.data).where(
            EstoqueDiaFinalizado.data.between(data_inicio, data_fim),
        )
    )).all()}

    items: list[dict[str, Any]] = []
    total_envios = 0
    total_conferido = 0
    for r in rows:
        dia_str = r.dia.isoformat() if r.dia else ""
        envios_n = int(r.envios or 0)
        conf = checks.get(dia_str, False)
        # `conferido_filter` is applied client-of-the-loop so totals
        # reflect the visible set only. `all` (or None) shows everything;
        # the other two narrow to one bucket.
        if conferido_filter == "conferidos" and not conf:
            continue
        if conferido_filter == "nao_conferidos" and conf:
            continue
        estoque_conferidos = estoque_checks_by_day.get(dia_str, 0)
        if r.dia in locks:
            # Dia travado: ignora o count atual de produtos.
            conferencia_estoque = "total"
        elif total_produtos == 0:
            conferencia_estoque = "nenhuma"
        elif estoque_conferidos >= total_produtos:
            conferencia_estoque = "total"
        elif estoque_conferidos > 0:
            conferencia_estoque = "parcial"
        else:
            conferencia_estoque = "nenhuma"
        items.append({
            "data": dia_str,
            "envios": envios_n,
            "conferido": conf,
            "conferencia_estoque": conferencia_estoque,
        })
        total_envios += envios_n
        if conf:
            total_conferido += envios_n

    return {
        "data": items,
        # Spec: rodapé "Total" conta SÓ os conferidos. `total_envios`
        # mantido pra "Total geral" se a UI quiser exibir.
        "total": total_conferido,
        "total_envios": total_envios,
        "total_conferido": total_conferido,
        "periodo": {"inicio": str(data_inicio), "fim": str(data_fim)},
    }


async def _count_active_products(
    session: AsyncSession,
    tags: list[str] | None,
    estoque_filter: str | None = None,
) -> int:
    """Total de produtos visíveis na aba Estoque pro usuário (mesmo
    filtro de situacao/formato/SKU+/baseline da list_estoque_produtos).
    Usado pra calcular % de conferência por dia. `estoque_filter` é
    forwardado pra manter o denominador alinhado com o que o operador
    vê na tela quando filtra por com/sem estoque."""
    where: list = [
        Product.situacao == "A",
        Product.formato == "S",
        Product.sku.notlike("%+%"),
        *_baseline_sku_exclusions(Product.sku),
    ]
    if tags is not None:
        where.append(or_(*[_sql_clause_for_tag(Product.sku, t) for t in tags]))
    stock_clause = _stock_filter_clause(estoque_filter)
    if stock_clause is not None:
        where.append(stock_clause)
    n = (
        await session.execute(
            select(func.count()).select_from(Product).where(and_(*where))
        )
    ).scalar_one()
    return int(n or 0)


async def _count_estoque_checks_by_day(
    session: AsyncSession,
    *,
    user_id,
    data_inicio: date,
    data_fim: date,
    tags: list[str] | None,
    estoque_filter: str | None = None,
    is_admin: bool = False,
) -> dict[str, int]:
    """{reference_date_iso: count_conferido_true} pra section='estoque'
    do usuário, no período. `tags` mirrors _count_active_products —
    sem isso, contar todos os checks do usuário cruzava tags (operador
    confere malas, troca pra SP e o dia aparecia 'Total' porque o
    numerador era o count global e o denominador era só SP).

    Filtro: StockCheck.reference_id é o SKU; aplicamos as mesmas
    regras de SKU usadas pelo tag-resolver + baseline exclusions pra
    ficar 1:1 com o COUNT de produtos ativos.

    `estoque_filter` é aplicado via JOIN em products — só quando
    requisitado (caso default não paga o custo do JOIN)."""
    where: list = [
        StockCheck.section == "estoque",
        StockCheck.conferido.is_(True),
        StockCheck.reference_date >= data_inicio,
        StockCheck.reference_date <= data_fim,
        *_baseline_sku_exclusions(StockCheck.reference_id),
    ]
    if not is_admin:
        where.append(StockCheck.user_id == user_id)
    if tags is not None:
        where.append(
            or_(*[_sql_clause_for_tag(StockCheck.reference_id, t) for t in tags])
        )

    # Admin agrega todos os usuários — conta SKUs DISTINTOS por dia pra
    # não duplicar quando 2 operadores conferem o mesmo SKU no mesmo dia.
    n_expr = (
        func.count(func.distinct(StockCheck.reference_id)) if is_admin else func.count()
    )
    stmt = (
        select(StockCheck.reference_date, n_expr.label("n"))
        .group_by(StockCheck.reference_date)
    )
    stock_clause = _stock_filter_clause(estoque_filter)
    if stock_clause is not None:
        # JOIN matches SKU exactly; checks for SKUs that no longer exist
        # in products silently drop, which is the right behaviour here
        # (stale checks shouldn't inflate the numerator).
        stmt = stmt.join(Product, Product.sku == StockCheck.reference_id).where(stock_clause)
    rows = (await session.execute(stmt.where(and_(*where)))).all()
    return {str(r.reference_date): int(r.n or 0) for r in rows}


@router.get("/conferencia-hoje")
async def conferencia_estoque_hoje(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("controle_estoque", "view"))],
    tag: str | None = Query(None),
    estoque_filter: str | None = Query(None, pattern="^(all|com|sem)$"),
) -> dict[str, Any]:
    """Foto da conferência da aba Estoque para HOJE — independente do
    dia que o operador está visualizando. Usado pelo bloqueio da aba
    Envios (só libera quando o dia atual está 100%)."""
    tags = _resolve_tags(user, tag)
    today = datetime.now(_BRT).date()
    total = await _count_active_products(session, tags, estoque_filter=estoque_filter)
    by_day = await _count_estoque_checks_by_day(
        session, user_id=user.id, data_inicio=today, data_fim=today,
        tags=tags, estoque_filter=estoque_filter,
    )
    conferido = by_day.get(today.isoformat(), 0)
    if total == 0:
        percent = 0
    else:
        percent = min(100, int(round(100 * conferido / total)))
    return {
        "data": today.isoformat(),
        "total": total,
        "conferido": conferido,
        "percent": percent,
    }


# ─── TOGGLE CONFERIDO ────────────────────────────────────────────────────


@router.post("/check")
async def toggle_estoque_check(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("controle_estoque", "edit"))],
    section: str = Query(..., pattern="^(estoque|pedido|envio)$"),
    reference_id: str = Query(...),
    reference_date: date = Query(...),
    conferido: bool = Query(...),
    observacao: str | None = Query(None),
) -> dict[str, Any]:
    # Envios tab is an admin-only triage view — operators see read-only
    # ✓/✗ but can't toggle. The other two sections are operator-editable.
    if section == "envio" and user.role != UserRole.ADMIN:
        raise HTTPException(403, detail={"code": "admin_only"})
    existing = (
        await session.execute(
            select(StockCheck).where(
                StockCheck.user_id == user.id,
                StockCheck.section == section,
                StockCheck.reference_id == reference_id,
                StockCheck.reference_date == reference_date,
            )
        )
    ).scalar_one_or_none()

    if existing is None:
        session.add(
            StockCheck(
                user_id=user.id,
                section=section,
                reference_id=reference_id,
                reference_date=reference_date,
                conferido=conferido,
                observacao=observacao,
            )
        )
    else:
        existing.conferido = conferido
        if observacao is not None:
            existing.observacao = observacao or None

    # Trava permanente do badge `conferencia_estoque` quando admin
    # finaliza o dia (✓ em section='envio'). Migration 0134. Lock NÃO
    # é removido se o admin destickar — é um carimbo "fechei esse dia",
    # não um espelho do estado atual.
    if (
        section == "envio"
        and conferido is True
        and user.role == UserRole.ADMIN
    ):
        stmt = (
            pg_insert(EstoqueDiaFinalizado)
            .values(data=reference_date)
            .on_conflict_do_nothing(index_elements=["data"])
        )
        await session.execute(stmt)

    await session.commit()
    return {"ok": True}


# ─── SYNC STOCKS (manual reload from Bling) ──────────────────────────────


@router.post("/sync-stocks")
async def sync_stocks(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("controle_estoque", "edit"))],
    tag: str | None = Query(None),
) -> dict[str, Any]:
    """Forces a fresh GET /estoques/saldos call on Bling for every
    product matching the current user/tag filter, then updates
    `Product.stock` + `Product.reserved_stock` in-place.

    Bling's webhook is reliable for most stock changes, but virtual
    balance updates triggered by reservas occasionally don't fire — the
    operator's "Reload" button calls this endpoint so they can force a
    fresh read after spotting a discrepancy.

    Batched in 50-id chunks (Bling allows up to 100 per call; 50 is
    a comfortable rate-limit safety margin). Soft-fails per chunk;
    returns the count of products it managed to refresh."""
    import asyncio

    from app.services.marketing.bling_revenue import _resolve_bling_client

    tags = _resolve_tags(user, tag)

    where: list = [
        Product.situacao == "A",
        Product.formato == "S",
        Product.sku.notlike("%+%"),
        Product.bling_product_id.isnot(None),
        *_baseline_sku_exclusions(Product.sku),
    ]
    if tags is not None:
        where.append(or_(*[_sql_clause_for_tag(Product.sku, t) for t in tags]))

    products = (
        await session.execute(
            select(Product).where(and_(*where))
        )
    ).scalars().all()
    if not products:
        return {"updated": 0, "missing_bling_data": 0, "total_products": 0}

    client = await _resolve_bling_client(session)
    if client is None:
        raise HTTPException(503, detail={"code": "bling_not_connected"})

    by_bling_id: dict[int, Product] = {
        int(p.bling_product_id): p for p in products if p.bling_product_id
    }
    bling_ids = list(by_bling_id.keys())

    updated = 0
    missing = 0
    chunk_size = 50
    for i in range(0, len(bling_ids), chunk_size):
        chunk = bling_ids[i : i + chunk_size]
        params: list[tuple[str, str]] = [("idsProdutos[]", str(bid)) for bid in chunk]
        try:
            r = await client._request("GET", "/estoques/saldos", params=params)
            r.raise_for_status()
            data = (r.json() or {}).get("data") or []
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "estoque_sync_bling_chunk_failed",
                err=str(e)[:200], chunk_start=i, chunk_len=len(chunk),
            )
            continue
        for row in data:
            prod_obj = (row.get("produto") or {})
            try:
                bid = int(prod_obj.get("id") or 0)
            except (TypeError, ValueError):
                continue
            p = by_bling_id.get(bid)
            if p is None:
                continue
            fisico = row.get("saldoFisicoTotal")
            virtual = row.get("saldoVirtualTotal")
            if virtual is None and fisico is None:
                missing += 1
                continue
            try:
                v = int(float(virtual)) if virtual is not None else int(p.stock or 0)
                f = int(float(fisico)) if fisico is not None else v
            except (TypeError, ValueError):
                missing += 1
                continue
            p.stock = v
            p.reserved_stock = max(0, f - v)
            updated += 1
        # Polite pacing between chunks — Bling's documented ceiling is
        # 3 req/s but bursts close to that have tripped us before.
        if i + chunk_size < len(bling_ids):
            await asyncio.sleep(0.4)

    await session.commit()
    logger.info(
        "estoque_sync_stocks", user_id=str(user.id), tags=tags,
        total_products=len(products), updated=updated, missing=missing,
    )
    return {
        "updated": updated,
        "missing_bling_data": missing,
        "total_products": len(products),
        "synced_at": datetime.now(UTC).isoformat(),
    }


# ─── SKU NOTE UPSERT (operator obs on no-movement day) ───────────────────


@router.post("/sku-obs")
async def upsert_sku_obs(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("controle_estoque", "edit"))],
    sku: str = Query(...),
    reference_date: date = Query(...),
    observacao: str | None = Query(None),
) -> dict[str, Any]:
    """Writes an obs against a SKU on `reference_date` even when no
    Bling-emitted entrada movement exists for that day. Used by the
    Estoque tab so the operator can leave a note ("Reposição prevista
    quarta", "Aguardando fornecedor") on any SKU regardless of the
    day's actual movements.

    Behavior:
      * If an entrada movement (`tipo='E'`) exists for the SKU within
        the day's UTC window, updates its observacao.
      * Otherwise inserts a placeholder movement (`tipo='E'`,
        `quantidade=0`, `origem='manual-note'`) carrying the obs. The
        next produtos GET surfaces it as a regular entrada with qty=0.

    Returns the resolved movement_id so the FE can reuse the
    movement-level PATCH endpoint on subsequent edits."""
    obs = (observacao or "").strip() or None
    window_start = datetime.combine(reference_date, time.min, tzinfo=UTC)
    window_end = datetime.combine(reference_date, time.max, tzinfo=UTC)

    existing = (
        await session.execute(
            select(StockMovement).where(
                StockMovement.sku == sku,
                StockMovement.tipo == "E",
                StockMovement.date >= window_start,
                StockMovement.date <= window_end,
            ).order_by(StockMovement.date.desc())
        )
    ).scalars().first()

    if existing is not None:
        existing.observacao = obs
        await session.commit()
        return {
            "ok": True,
            "movement_id": str(existing.id),
            "observacao": existing.observacao,
            "created": False,
        }

    # Need bling_product_id (NOT NULL on the movement) — look up via SKU.
    bling_product_id = (
        await session.execute(
            select(Product.bling_product_id).where(Product.sku == sku)
        )
    ).scalar_one_or_none()
    if bling_product_id is None:
        raise HTTPException(404, detail={"code": "sku_not_found"})

    m = StockMovement(
        bling_product_id=int(bling_product_id),
        sku=sku,
        tipo="E",
        quantidade=0,
        observacao=obs,
        origem="manual-note",
        date=datetime.now(UTC),
    )
    session.add(m)
    await session.commit()
    await session.refresh(m)
    return {
        "ok": True,
        "movement_id": str(m.id),
        "observacao": m.observacao,
        "created": True,
    }


# ─── MOVEMENT OBS PATCH (operator inline-edit) ───────────────────────────


@router.patch("/movement/{movement_id}/obs")
async def patch_movement_obs(
    movement_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("controle_estoque", "edit"))],
    observacao: str | None = Query(None),
) -> dict[str, Any]:
    """Bling's estoque webhook doesn't carry the 'observação' field that
    the operator's planilha shows in the 'Responsável' column. This
    endpoint lets the operator write that value in. Soft validation —
    we only require the movement to exist and the user to have edit
    permission; tag-scoping is enforced by /api/estoque/produtos which
    is the only way the operator discovers movement IDs."""
    m = await session.get(StockMovement, movement_id)
    if m is None:
        raise HTTPException(404, detail={"code": "movement_not_found"})
    m.observacao = (observacao or "").strip() or None
    await session.commit()
    return {"ok": True, "movement_id": str(m.id), "observacao": m.observacao}


# ─── ESTOQUE NEGATIVO / SUFIXOS / REFRESH BLING ───────────────────────────
#
# Migration from the standalone xml-up container into the DaVinci API.
# Reads from products.saldo_fisico / saldo_virtual_total — both populated
# by /atualizar-bling (which hits Bling's GET /estoques/saldos directly),
# distinct from products.stock / reserved_stock (which come from
# webhooks and may lag). The operator uses these views to spot
# inventory drift before printing shipping labels.


def _negativos_base_where() -> list:
    """Shared filter: ativos OR situacao desconhecido, sem kits (+)."""
    return [
        or_(Product.situacao == "A", Product.situacao.is_(None)),
        Product.sku.notlike("%+%"),
    ]


_ESTOQUE_NEGATIVO_EMAILS = frozenset({"sa.geral@tutamail.com"})
_ESTOQUE_NEGATIVO_NAMES = frozenset({"churchill"})


def _require_estoque_negativo_access(user: User) -> None:
    """Acesso à aba "Estoque Negativo" restrito a admin, gerente (churchill)
    e cairo SA (sa.geral@tutamail.com). Demais operadores recebem 403 —
    a tab nem aparece no frontend, mas garantimos no backend também."""
    if user.role == UserRole.ADMIN:
        return
    if (user.email or "").lower() in _ESTOQUE_NEGATIVO_EMAILS:
        return
    if (getattr(user, "name", None) or "").lower() in _ESTOQUE_NEGATIVO_NAMES:
        return
    raise HTTPException(403, detail={"code": "estoque_negativo_forbidden"})


@router.get("/negativos")
async def list_estoque_negativos(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("controle_estoque", "view"))],
    search: str | None = Query(None),
) -> dict[str, Any]:
    """Produtos com saldo_virtual_total < 0 — visão global, sem tag.
    Acesso restrito (admin/churchill/cairo SA).
    Operador remove esses da fila de etiquetas antes do envio."""
    _require_estoque_negativo_access(user)
    where = _negativos_base_where()
    where.append(Product.saldo_virtual_total < 0)
    if search:
        where.append(Product.sku.ilike(f"%{search.strip()}%"))

    rows = (
        await session.execute(
            select(Product.sku, Product.saldo_fisico, Product.saldo_virtual_total)
            .where(and_(*where))
            .order_by(Product.saldo_fisico.desc().nulls_last(), Product.sku.asc())
        )
    ).all()
    items = [
        {
            "codigo": r.sku,
            "saldo_fisico": int(r.saldo_fisico or 0),
            "saldo_virtual_total": int(r.saldo_virtual_total or 0),
        }
        for r in rows
    ]
    return {"success": True, "items": items, "total": len(items)}


@router.get("/sufixos")
async def list_estoque_sufixos(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("controle_estoque", "view"))],
    suffixes: str = Query(default=".us,.sa"),
) -> dict[str, Any]:
    """Produtos cujo SKU termina nos sufixos passados (comma-separated).
    Apenas saldo_fisico > 0, ordenado por saldo desc.
    Acesso restrito (admin/churchill/cairo SA) — anexo à aba Estoque Negativo."""
    _require_estoque_negativo_access(user)
    sufs = [s.strip() for s in suffixes.split(",") if s.strip()]
    if not sufs:
        return {"success": True, "items": [], "total": 0, "suffixes": []}
    where = _negativos_base_where()
    where.append(Product.saldo_fisico > 0)
    where.append(or_(*[Product.sku.ilike(f"%{s}") for s in sufs]))
    rows = (
        await session.execute(
            select(Product.sku, Product.saldo_fisico, Product.saldo_virtual_total)
            .where(and_(*where))
            .order_by(Product.saldo_fisico.desc().nulls_last(), Product.sku.asc())
        )
    ).all()
    items = [
        {
            "codigo": r.sku,
            "saldo_fisico": int(r.saldo_fisico or 0),
            "saldo_virtual_total": int(r.saldo_virtual_total or 0),
        }
        for r in rows
    ]
    return {"success": True, "items": items, "total": len(items), "suffixes": sufs}


@router.get("/sufixos.csv")
async def export_estoque_sufixos_csv(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("controle_estoque", "view"))],
    suffixes: str = Query(default=".us,.sa"),
) -> Response:
    """CSV exportável (separador `;` + BOM UTF-8 pra abrir limpo no Excel BR).
    Acesso restrito (admin/churchill/cairo SA)."""
    _require_estoque_negativo_access(user)
    import csv as _csv
    import io as _io
    sufs = [s.strip() for s in suffixes.split(",") if s.strip()]
    where = _negativos_base_where()
    where.append(Product.saldo_fisico > 0)
    if sufs:
        where.append(or_(*[Product.sku.ilike(f"%{s}") for s in sufs]))
    rows = (
        await session.execute(
            select(Product.sku, Product.saldo_fisico, Product.saldo_virtual_total)
            .where(and_(*where))
            .order_by(Product.saldo_fisico.desc().nulls_last(), Product.sku.asc())
        )
    ).all()
    buf = _io.StringIO()
    w = _csv.writer(buf, delimiter=";")
    w.writerow(["codigo", "saldo_fisico", "saldo_virtual_total"])
    for r in rows:
        w.writerow([r.sku, int(r.saldo_fisico or 0), int(r.saldo_virtual_total or 0)])
    fname = f"estoque_sufixos_{datetime.now(UTC).strftime('%Y%m%d_%H%M')}.csv"
    return Response(
        content="﻿" + buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


# Process-local lock for the refresh. Single-process deploy on prod
# (uvicorn workers=1 per container) so an in-memory flag is enough.
# Persisted in a dict so the status endpoint can read it without
# importing private state.
_refresh_state: dict[str, Any] = {"running": False, "started_at": None}


@router.get("/atualizar/status")
async def estoque_atualizar_status(
    _u: Annotated[User, Depends(require_permission("controle_estoque", "view"))],
) -> dict[str, Any]:
    return {
        "running": _refresh_state["running"],
        "started_at": _refresh_state["started_at"],
    }


@router.post("/atualizar-bling")
async def estoque_atualizar_bling(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("controle_estoque", "edit"))],
) -> dict[str, Any]:
    """Pull fresh saldoFisico / saldoVirtual from Bling for every active
    simples SKU, write to products.saldo_fisico + saldo_virtual_total.
    Distinct from /sync-stocks which writes to stock + reserved_stock
    (the webhook-target fields). Use this one when the operator suspects
    webhook drift before generating shipping labels.

    Batched 50/call, 0.8s sleep between batches to stay under Bling's
    3 req/s ceiling. Single-flight via _refresh_state — returns 409 if
    another caller already started it."""
    import asyncio

    from app.services.marketing.bling_revenue import _resolve_bling_client

    if _refresh_state["running"]:
        raise HTTPException(409, detail={
            "code": "refresh_already_running",
            "started_at": _refresh_state["started_at"],
        })

    _refresh_state["running"] = True
    _refresh_state["started_at"] = datetime.now(UTC).isoformat()
    try:
        where = [
            or_(Product.situacao == "A", Product.situacao.is_(None)),
            Product.sku.notlike("%+%"),
            Product.bling_product_id.isnot(None),
        ]
        products = (
            await session.execute(select(Product).where(and_(*where)))
        ).scalars().all()
        if not products:
            return {"success": True, "updated": 0, "total_products": 0}

        client = await _resolve_bling_client(session)
        if client is None:
            raise HTTPException(503, detail={"code": "bling_not_connected"})

        by_bling_id: dict[int, Product] = {
            int(p.bling_product_id): p for p in products if p.bling_product_id
        }
        bling_ids = list(by_bling_id.keys())
        updated = 0
        missing = 0
        chunk_size = 50
        for i in range(0, len(bling_ids), chunk_size):
            chunk = bling_ids[i : i + chunk_size]
            params: list[tuple[str, str]] = [("idsProdutos[]", str(bid)) for bid in chunk]
            try:
                r = await client._request("GET", "/estoques/saldos", params=params)
                r.raise_for_status()
                data = (r.json() or {}).get("data") or []
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "estoque_atualizar_bling_chunk_failed",
                    err=str(e)[:200], chunk_start=i, chunk_len=len(chunk),
                )
                continue
            for row in data:
                prod_obj = (row.get("produto") or {})
                try:
                    bid = int(prod_obj.get("id") or 0)
                except (TypeError, ValueError):
                    continue
                p = by_bling_id.get(bid)
                if p is None:
                    continue
                depositos = row.get("depositos") or []
                if depositos:
                    fisico = sum(int(d.get("saldoFisico") or 0) for d in depositos)
                    virtual = sum(int(d.get("saldoVirtual") or 0) for d in depositos)
                else:
                    fisico_raw = row.get("saldoFisicoTotal")
                    virtual_raw = row.get("saldoVirtualTotal")
                    if fisico_raw is None and virtual_raw is None:
                        missing += 1
                        continue
                    fisico = int(float(fisico_raw)) if fisico_raw is not None else 0
                    virtual = int(float(virtual_raw)) if virtual_raw is not None else 0
                p.saldo_fisico = fisico
                p.saldo_virtual_total = virtual
                updated += 1
            if i + chunk_size < len(bling_ids):
                await asyncio.sleep(0.8)
        await session.commit()
        logger.info(
            "estoque_atualizar_bling_done",
            user_id=str(user.id), total_products=len(products),
            updated=updated, missing=missing,
        )
        return {
            "success": True,
            "requested": len(bling_ids),
            "updated": updated,
            "missing_bling_data": missing,
            "total_products": len(products),
        }
    finally:
        _refresh_state["running"] = False
        _refresh_state["started_at"] = None
