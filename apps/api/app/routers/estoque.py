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
from pydantic import BaseModel, Field
from sqlalchemy import Date, and_, cast, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_permission
from app.models import (
    BlingEnvioEvento,
    BlingOrder,
    EstoqueDiaFinalizado,
    PrevisaoImpressa,
    Product,
    User,
    UserRole,
)
from app.models.company import Store
from app.models.integration import Integration
from app.models.nf import NfEtiquetaArquivo
from app.models.stock_check import StockCheck
from app.models.stock_movement import StockMovement
from app.services.estoque_relatorio_pdf import relatorio_pedidos_pdf
from app.services.nf_etiqueta_juntar import (
    EtiquetaJuntarError,
    juntar_etiqueta_nf,
    juntar_varios,
)
from app.services.sku_tags import VALID_TAGS as _VALID_TAGS
from app.services.sku_tags import classify_sku_tag as _classify_sku_tag
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

# Regra de "enviado" (decidida pelo dono): enviado = em_andamento_data
# preenchida E situacao NOT IN (cancelamento/pré-envio). Em vez de uma
# allowlist (que silenciosamente derruba pedidos quando avançam pra uma
# situação pós-envio nova — Entregue, Resolvido, Manutenção, Problemas,
# Perdimento…), usamos uma BLOCKLIST: tudo com data de envio conta, menos
# o que claramente não saiu. À prova de novas situações pós-envio.
_SITUACAO_NAO_ENVIADO = (
    "6",      # Em aberto
    "12",     # Cancelado
    "21",     # Em digitação
    "83955",  # Aguardando Cancelamento
    "83962",  # Verificar Cancelamento
    "83966",  # Erro no Envio
    "84686",  # Golpe
    "545901", # Sucata
)
# Badge VERDE (enviado confirmado) = enviado E não é a etiqueta provisória
# 83965 (que segue vermelha até a agência confirmar).
_SITUACAO_NAO_VERDE = _SITUACAO_NAO_ENVIADO + (_SITUACAO_ENVIADO_ETIQUETA,)

# Pedidos pendentes mais antigos que isso = zumbis (webhook perdido) —
# ficam escondidos da aba.
_PENDENTE_MAX_AGE_DIAS = 14

# Situação Bling "Em aberto" — pedido entrou, NF ainda não emitida e
# etiqueta ainda não gerada. Vira o badge AMARELO "previsão" da aba
# Pedidos (Eduardo, 2026-08-24): o pessoal do envio separa o produto de
# manhã e, quando a etiqueta liberar (ML solta ~meio-dia), é só colar.
# O pedido troca de badge sozinho conforme avança no Bling.
# Conta como previsão quem é para enviar HOJE ou AMANHÃ (Eduardo, 2026-08-24
# "previsao somente dos que e para enviar no dia"; ampliado em 2026-08-26
# "aparecer as previsoes do dia de amanha tbm" — o pessoal já separa hoje o
# que sai amanhã): marketplace_ship_deadline (o "despachar até" prometido ao
# marketplace) até amanhã BRT. Pedido com corte depois de amanhã fica fora
# até chegar a vez dele; sem deadline capturado também fica fora (não dá
# pra afirmar quando sai).
# ⚠️ A janela é pelo CORTE, não pela data de criação: pedido de
# terça–sexta com corte segunda (fim de semana rola pro próximo dia
# útil) é previsão legítima — janela por criação escondia 15 malas
# reais com corte no dia (bug pego pelo Eduardo em 2026-08-24).
_SITUACAO_EM_ABERTO = "6"
# Corte pode estar atrasado até isso e o pedido ainda conta como previsão
# (precisa sair MESMO ASSIM). Mais atrasado que isso = zumbi/problema,
# some da previsão pra não poluir a aba pra sempre.
_PREVISAO_CORTE_ATRASO_MAX_DIAS = 2
# Quantos dias de corte FUTURO entram na previsão: 1 = mostra também os de
# corte amanhã (Eduardo, 2026-08-26 "aparecer as previsoes do dia de amanha
# tbm... os de amanha pra ja ir adiantando"). O front marca cada linha com
# hoje/amanhã pelo ship_deadline, então o pessoal sabe o que sai já e o que
# é adiantamento. 0 = volta a ser só o dia.
_PREVISAO_CORTE_FUTURO_MAX_DIAS = 1


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


def _active_simple_product_clauses() -> list:
    """situacao/formato NULL contam como ativo/simples — mesma convenção
    do resto do app (devoluções, importação, estoque negativo já usam
    `or_(== , is_(None))`). Produtos recém-importados do Bling entram com
    esses campos NULL (o importer nem sempre popula) e não podem sumir do
    Controle de Estoque só por isso — o próximo refresh do Bling backfilla
    situacao/formato. Kits ainda são barrados: formato='E' não casa e o
    filtro de SKU com '+' pega os compostos."""
    return [
        or_(Product.situacao == "A", Product.situacao.is_(None)),
        or_(Product.formato == "S", Product.formato.is_(None)),
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


def _pedidos_todas_tags(user: User) -> bool:
    """True se o user enxerga TODAS as tags no GET /pedidos — papel de
    "gerente de etiquetas" (cairo SA): é ele quem imprime e despacha as
    etiquetas do time inteiro, então a aba Pedidos precisa listar tudo.

    Flag: permissions['controle_estoque_pedidos_todas_tags']=true.
    Vale SÓ para a listagem de pedidos (e, por consequência, pros botões
    de imprimir — que já não cercam por tag). Estoque, Envios e
    conferências continuam cercados pelas stock_tags do usuário."""
    perms = user.permissions or {}
    return bool(perms.get("controle_estoque_pedidos_todas_tags", False))


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


def _tags_pedidos(user: User, tag: str | None) -> list[str] | None:
    """Tags que o chamador enxerga na aba Pedidos (None = todas).

    Gerente de etiquetas não tem cerca aqui (vê o time inteiro), mas o
    dropdown de tag continua valendo como sub-seleção."""
    if user.role != UserRole.ADMIN and _pedidos_todas_tags(user):
        ov = (tag or "").strip().lower()
        if ov and ov not in _VALID_TAGS:
            raise HTTPException(400, detail={"code": "invalid_tag"})
        return [ov] if ov else None
    return _resolve_tags(user, tag)


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
    # Dia PASSADO (data_fim < hoje SP): a coluna Saldo vem do valor
    # CONGELADO na conferência (stock_checks.saldo_virtual/reserved), não do
    # products.stock ao vivo. Hoje/futuro segue ao vivo. A aba manda
    # data_inicio == data_fim (dia único), então data_fim é o "dia" olhado.
    today = datetime.now(_BRT).date()
    is_historical = data_fim < today

    # `formato='S'` should already exclude kits, but prod data has some
    # compound SKUs ("x009.ci+a001.ci") sneaking through with the
    # simples flag set incorrectly. Belt-and-suspenders: drop anything
    # whose SKU contains a '+' character regardless of `formato`.
    where: list = [
        *_active_simple_product_clauses(),
        Product.sku.notlike("%+%"),
        *_baseline_sku_exclusions(Product.sku),
    ]
    if tags is not None:
        # OR across each tag's pattern — operator with [ci, ra] sees
        # products ending in .ci OR .ra.
        where.append(or_(*[_sql_clause_for_tag(Product.sku, t) for t in tags]))
    # Em dia passado o saldo efetivo vem do congelado, então o filtro
    # com/sem-estoque roda em Python (abaixo), sobre o saldo congelado — não
    # no SQL sobre products.stock (ao vivo). Hoje: filtra no SQL, ao vivo.
    stock_clause = _stock_filter_clause(estoque_filter)
    if stock_clause is not None and not is_historical:
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

    # Dia passado: puxa o saldo CONGELADO na conferência daquele dia. Só
    # existe pros SKUs que foram ticados conferido (a rotina confere tudo
    # todo dia → cobertura total). `updated_at` asc: a conferência mais
    # recente sobrescreve no dict (relevante quando o admin/gerente vê as
    # conferências de vários operadores agregadas). Não-admin: só as suas.
    frozen: dict[str, tuple[int, int]] = {}
    if is_historical and skus:
        frozen_where = [
            StockCheck.section == "estoque",
            StockCheck.reference_date == data_fim,
            StockCheck.conferido.is_(True),
            StockCheck.saldo_virtual.isnot(None),
            StockCheck.reference_id.in_(skus),
        ]
        if not _user_sees_all_checks(user):
            frozen_where.append(StockCheck.user_id == user.id)
        frozen_rows = (
            await session.execute(
                select(
                    StockCheck.reference_id,
                    StockCheck.saldo_virtual,
                    StockCheck.reserved,
                )
                .where(and_(*frozen_where))
                .order_by(StockCheck.updated_at.asc())
            )
        ).all()
        frozen = {
            r.reference_id: (int(r.saldo_virtual or 0), int(r.reserved or 0))
            for r in frozen_rows
        }

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
        # Dia passado + SKU conferido → saldo CONGELADO daquele dia.
        # Senão (hoje, ou dia passado sem conferência) → saldo ao vivo.
        congelado = p.sku in frozen  # `frozen` só é populado em dia passado
        if congelado:
            virtual, reserved = frozen[p.sku]
        else:
            virtual = int(p.stock or 0)
            reserved = int(p.reserved_stock or 0)
        # Dia passado: com/sem-estoque filtra pelo saldo EFETIVO (o clause
        # SQL sobre products.stock foi pulado lá em cima justamente por isto).
        if is_historical and estoque_filter == "com" and virtual <= 0:
            continue
        if is_historical and estoque_filter == "sem" and virtual > 0:
            continue
        # `Product.stock` is the VIRTUAL balance (Bling saldoVirtualTotal).
        # The operator's "saldo atual" column wants the FÍSICO total —
        # virtual + reserved reconstructs that.
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
            "saldo_congelado": congelado,
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
    status_filter: str | None = Query(None, alias="status"),  # enviado | nao_enviado | previsao
    tag: str | None = Query(None),
) -> dict[str, Any]:
    """Lista pedidos da aba via 3 caminhos paralelos:
      * PREVISÃO: situacao=6 (Em aberto) + corte até AMANHÃ (inclui
        atrasado recente) → badge amarelo. Ainda sem NF/etiqueta; entra na
        lista pra equipe separar o produto antes de a etiqueta liberar
        (ML ~meio-dia); os de corte amanhã são adiantamento.
      * PENDENTE: situacao=83965 (Enviado Etiqueta) + sem em_andamento_data
        + criado nos últimos 14 dias (anti-zumbi) → badge vermelho.
      * ENVIADO:  situacao IN (83965, 15, 83953, 83957, 545902) +
        em_andamento_data preenchida → badge verde (exceto 83965, que fica
        vermelho mesmo com data). 83957=Aguardando Devolução e 545902=
        Resolvido continuam visíveis: pedido já saiu do estoque, fluxo de
        devolução é tratado em outra aba.
    Qualquer outra combinação fica escondida: `15` sem em_andamento_data
    é anomalia (já atendido, não é "não enviado"); 83965 com >14 dias é
    zumbi de webhook perdido; "Em aberto" com >2 dias é pedido-problema;
    outras situações custom não pertencem ao fluxo. Data de referência:
    ship date se confirmado, senão HOJE (BRT) — pendente e previsão
    "fixam" no dia atual e aparecem todo dia até avançar."""
    # Gerente de etiquetas: sem cerca de tag AQUI (e só aqui — o /produtos
    # e o /envios continuam passando por _resolve_tags).
    tags = _tags_pedidos(user, tag)
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
        # Blocklist: visível se tem data de envio e não é cancelamento/
        # pré-envio. 83965 (etiqueta) segue visível aqui, badge vermelho.
        BlingOrder.situacao.notin_(_SITUACAO_NAO_ENVIADO),
        BlingOrder.em_andamento_data.isnot(None),
    )
    # Janela do corte em BRT: [hoje - atraso_max 00:00, hoje + 1 + futuro_max
    # 00:00) — pega atrasado recente, HOJE e AMANHÃ.
    # Comparação direta timestamptz vs literal tz-aware: NULL fica fora.
    _fim_janela_brt = datetime.combine(
        today_brt + timedelta(days=1 + _PREVISAO_CORTE_FUTURO_MAX_DIAS),
        time.min,
        tzinfo=_BRT,
    )
    _corte_min_brt = datetime.combine(
        today_brt - timedelta(days=_PREVISAO_CORTE_ATRASO_MAX_DIAS), time.min, tzinfo=_BRT
    )
    previsao_clause = and_(
        # Em aberto = previsão do dia (badge amarelo). Sem exigir
        # em_andamento_data (situação 6 é pré-emissão, o campo é nulo).
        BlingOrder.situacao == _SITUACAO_EM_ABERTO,
        # Corte de despacho até AMANHÃ ("despachar até" do marketplace) —
        # inclui atrasado recente, hoje e amanhã (adiantamento); corte
        # depois de amanhã ou sem deadline capturado → fora. Janela pelo
        # CORTE, não pela criação (pedido de sexta com corte segunda é
        # previsão legítima).
        BlingOrder.marketplace_ship_deadline >= _corte_min_brt,
        BlingOrder.marketplace_ship_deadline < _fim_janela_brt,
    )
    where: list = [or_(pendente_clause, enviado_clause, previsao_clause)]
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
    # Etiqueta já no sistema (nf_etiqueta_arquivo, por numero)? Um "Em
    # aberto" estagnado pode JÁ ter etiqueta: o sync da situação roda a
    # cada ~2h e o Bling anda na frente (caso 291919 — badge "Previsão"
    # com botão Imprimir do lado, Eduardo 2026-08-24). A etiqueta é prova
    # de que a fase de previsão acabou → conta como vermelho.
    tem_etiqueta = (
        select(NfEtiquetaArquivo.pedido_bling)
        .where(NfEtiquetaArquivo.pedido_bling == BlingOrder.numero)
        .exists()
    )
    if status_filter == "previsao":
        # Amarelo = Em aberto AINDA SEM etiqueta.
        where.append(BlingOrder.situacao == _SITUACAO_EM_ABERTO)
        where.append(~tem_etiqueta)
        order_by = effective_date.desc()
    elif status_filter == "nao_enviado":
        # Vermelho = 83965 OU o 6 estagnado cuja etiqueta já chegou.
        where.append(
            or_(
                BlingOrder.situacao == _SITUACAO_ENVIADO_ETIQUETA,
                and_(BlingOrder.situacao == _SITUACAO_EM_ABERTO, tem_etiqueta),
            )
        )
        order_by = effective_date.desc()
    elif status_filter == "enviado":
        # Verde = enviado confirmado (exclui cancelamento/pré-envio E a
        # etiqueta provisória 83965).
        where.append(BlingOrder.situacao.notin_(_SITUACAO_NAO_VERDE))
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

    # Etiqueta transformada disponível? (landing zone da NF automática — a
    # etapa de visualização grava o blob em nf_etiqueta_arquivo por pedido).
    # Uma query, chaveada por pedido_bling (= o.numero). O botão "Imprimir
    # Etiqueta" só aparece/habilita quando existe blob pro pedido.
    # `created_at` = quando a etiqueta chegou (a linha nasce com o blob). A tela
    # mostra a hora pra o operador saber há quanto tempo está pronta pra imprimir.
    numeros = {o.numero for o in orders if o.numero}
    etiquetas_por_pedido: dict[str, datetime] = {}
    # Quando a etiqueta já foi impressa (NULL = nunca). É o que segura a
    # duplicidade na impressão em lote: a tela marca "Impressa" e o operador
    # deixa de selecionar de novo sem querer.
    impressa_por_pedido: dict[str, datetime] = {}
    if numeros:
        et_rows = (
            await session.execute(
                select(
                    NfEtiquetaArquivo.pedido_bling,
                    NfEtiquetaArquivo.created_at,
                    NfEtiquetaArquivo.impressa_em,
                ).where(NfEtiquetaArquivo.pedido_bling.in_(numeros))
            )
        ).all()
        etiquetas_por_pedido = {r.pedido_bling: r.created_at for r in et_rows}
        impressa_por_pedido = {
            r.pedido_bling: r.impressa_em for r in et_rows if r.impressa_em
        }

    # Papel de PREVISÃO já impresso? (previsao_impressa, por numero — o 🖨
    # da aba carimba via POST /pedidos/previsoes/impressas). A tela mostra a
    # hora ao lado do selo amarelo: quem já saiu no papel não é separado de
    # novo (Eduardo, 2026-08-26).
    previsao_impressa_por_pedido: dict[str, datetime] = {}
    if numeros:
        pi_rows = (
            await session.execute(
                select(
                    PrevisaoImpressa.pedido_bling, PrevisaoImpressa.impressa_em
                ).where(PrevisaoImpressa.pedido_bling.in_(numeros))
            )
        ).all()
        previsao_impressa_por_pedido = {r.pedido_bling: r.impressa_em for r in pi_rows}

    # Pedido que sai de 2+ armazéns (itens com tags de armazém diferentes).
    # A tela avisa "Atenção: estoque compartilhado" ao clicar em imprimir a
    # etiqueta. Consulta SEM a cerca de tag de propósito: o operador cercado
    # só vê o próprio item na listagem, mas o aviso precisa considerar o
    # pedido inteiro.
    compartilhado_por_pedido: set[str] = set()
    if numeros:
        sku_rows = (
            await session.execute(
                select(BlingOrder.numero, BlingOrder.item_codigo)
                .where(BlingOrder.numero.in_(numeros))
                .distinct()
            )
        ).all()
        tags_por_pedido: dict[str, set[str]] = {}
        for num, sku in sku_rows:
            t = _classify_sku_tag(sku)
            if t:
                tags_por_pedido.setdefault(num, set()).add(t)
        compartilhado_por_pedido = {
            n for n, ts in tags_por_pedido.items() if len(ts) >= 2
        }

    # Hora do ENVIO (coluna "Envio" da tela): instante em que o pedido entrou
    # na situação 15 ("em andamento"), lido do ledger bling_envio_evento
    # (trigger de banco — migration 0156). Pedido antigo (pré-ledger) não tem
    # evento → enviado_em fica null e a tela cai no rótulo "Enviado" de
    # sempre. MIN() porque o ledger tem grão de item; todos os itens do
    # pedido entram em 15 no mesmo instante.
    enviado_em_por_bling_id: dict[int, datetime] = {}
    bling_ids = {o.bling_id for o in orders if o.bling_id}
    if bling_ids:
        ev_rows = (
            await session.execute(
                select(
                    BlingEnvioEvento.bling_id,
                    func.min(BlingEnvioEvento.occurred_at).label("occurred_at"),
                )
                .where(BlingEnvioEvento.bling_id.in_(bling_ids))
                .group_by(BlingEnvioEvento.bling_id)
            )
        ).all()
        enviado_em_por_bling_id = {r.bling_id: r.occurred_at for r in ev_rows}

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
            # Nome de quem comprou (destinatário do pedido no Bling). Usado
            # na coluna Cliente da aba Pedidos e no relatório imprimível.
            "cliente": o.nome_destinatario,
            "sku": o.item_codigo,
            "produto": o.item_descricao,
            "quantidade": o.item_quantidade or 1,
            # Badge por situacao (não por em_andamento_data), via blocklist:
            #   - amarelo: 6 (Em aberto — previsão do dia) E SEM etiqueta
            #     no sistema. Se a etiqueta já chegou, o Bling está na
            #     frente do sync (~2h) e o pedido cai no vermelho — não
            #     pode ter "Previsão" com botão Imprimir do lado (291919).
            #   - vermelho: 83965 (etiqueta gerada, agência não confirmou)
            #     e situações de cancelamento/pré-envio (_SITUACAO_NAO_VERDE).
            #   - verde: todo o resto que tem data de envio — inclui
            #     Entregue, Resolvido, Aguardando Devolução, Manutenção,
            #     Problemas, Perdimento (foram enviados no dia).
            "status": "previsao"
            if (
                o.situacao == _SITUACAO_EM_ABERTO
                and o.numero not in etiquetas_por_pedido
            )
            else "nao_enviado"
            if o.situacao in _SITUACAO_NAO_VERDE
            else "enviado",
            "conferido": check["conferido"],
            "observacao": check["observacao"],
            "bling_id": o.bling_id,
            "etiqueta_disponivel": bool(o.numero) and o.numero in etiquetas_por_pedido,
            # Pedido dividido entre armazéns — a tela pede confirmação
            # ("Atenção: estoque compartilhado") antes de imprimir.
            "estoque_compartilhado": (
                bool(o.numero) and o.numero in compartilhado_por_pedido
            ),
            "etiqueta_em": (
                etiquetas_por_pedido[o.numero].isoformat()
                if o.numero in etiquetas_por_pedido
                else None
            ),
            "etiqueta_impressa_em": (
                impressa_por_pedido[o.numero].isoformat()
                if o.numero in impressa_por_pedido
                else None
            ),
            # Instante em que o envio confirmou (entrada na situação 15,
            # via ledger). Null: pedido não enviado ou anterior ao ledger.
            "enviado_em": (
                enviado_em_por_bling_id[o.bling_id].isoformat()
                if o.bling_id and o.bling_id in enviado_em_por_bling_id
                else None
            ),
            # "Despachar até" prometido ao marketplace (horário de corte do
            # pedido), capturado pelo sweep de envio direto da API de cada
            # plataforma. ISO tz-aware (UTC) ou null. A tela só mostra
            # quando status != enviado.
            "ship_deadline": (
                o.marketplace_ship_deadline.isoformat()
                if o.marketplace_ship_deadline
                else None
            ),
            # Quando o papel de previsão deste pedido saiu na impressora
            # (null = nunca). A tela mostra "🖨 HH:MM" sob o selo amarelo.
            "previsao_impressa_em": (
                previsao_impressa_por_pedido[o.numero].isoformat()
                if o.numero and o.numero in previsao_impressa_por_pedido
                else None
            ),
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


def _pdf_para_impressao(row: NfEtiquetaArquivo) -> bytes:
    """PDF final de UM pedido: etiqueta, ou etiqueta + NF quando é correios.

    A presença de `nf_pdf` é o sinal do fluxo correios/ML (não aceita declaração
    de conteúdo, vai a NF junto). Falha na junção degrada pra só a etiqueta —
    melhor imprimir a etiqueta sozinha do que travar o despacho.
    """
    if not row.nf_pdf:
        return row.blob
    try:
        return juntar_etiqueta_nf(row.blob, row.nf_pdf)
    except EtiquetaJuntarError:
        logger.warning("nf_etiqueta_juntar_falhou", pedido_bling=row.pedido_bling)
        return row.blob


class PrevisoesImpressasIn(BaseModel):
    """Pedidos cujo papel de previsão acabou de sair na impressora."""

    pedidos: list[str] = Field(min_length=1)


@router.post("/pedidos/previsoes/impressas")
async def marcar_previsoes_impressas(
    payload: PrevisoesImpressasIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("controle_estoque", "view"))],
) -> dict[str, Any]:
    """Carimba "papel de previsão impresso" nos pedidos (upsert por numero).

    Chamado pelo front logo depois do 🖨 do relatório de previsões abrir o
    diálogo de impressão. Reimpressão re-carimba `impressa_em` (a tela mostra
    sempre a última). Permissão "view" de propósito: quem consegue ver e
    imprimir o papel pode carimbar — não é edição de dado de negócio.
    """
    numeros = sorted({(n or "").strip() for n in payload.pedidos} - {""})
    if not numeros:
        return {"marcados": 0}
    stmt = (
        pg_insert(PrevisaoImpressa)
        .values([{"pedido_bling": n} for n in numeros])
        .on_conflict_do_update(
            index_elements=["pedido_bling"],
            set_={"impressa_em": func.now()},
        )
    )
    await session.execute(stmt)
    await session.commit()
    return {"marcados": len(numeros)}


class EtiquetasLoteIn(BaseModel):
    """Pedidos selecionados na aba Pedidos pra imprimir de uma vez."""

    pedidos: list[str] = Field(min_length=1)
    # Anexa o relatório de conferência como últimas páginas do PDF
    # (etiquetas em cima, relatório embaixo — um documento só).
    incluir_relatorio: bool = False


async def _linhas_relatorio(
    session: AsyncSession, pedidos: list[str],
) -> list[dict[str, Any]]:
    """Monta as linhas do relatório dos pedidos (1 linha por item).

    Reconsulta o banco em vez de confiar na tela: o PDF impresso é o
    documento de conferência, então tem que refletir o que está gravado.
    Ordena por cliente (é assim que o operador separa os pacotes).
    """
    orders = (
        await session.execute(
            select(BlingOrder)
            .where(BlingOrder.numero.in_(pedidos))
            .order_by(
                BlingOrder.nome_destinatario,
                BlingOrder.numero,
                BlingOrder.item_index,
            )
        )
    ).scalars().all()
    if not orders:
        return []

    store_ids: set[int] = set()
    for o in orders:
        try:
            store_ids.add(int(o.loja))
        except (TypeError, ValueError):
            continue
    store_name_by_id: dict[int, str] = {}
    if store_ids:
        for r in (
            await session.execute(
                select(Store.bling_store_id, Integration.name, Integration.platform)
                .join(Integration, Integration.id == Store.integration_id, isouter=True)
                .where(Store.bling_store_id.in_(store_ids))
            )
        ).all():
            try:
                bsid = int(r.bling_store_id)
            except (TypeError, ValueError):
                continue
            plat = (
                r.platform.value if hasattr(r.platform, "value") else str(r.platform or "")
            ).strip()
            label = (r.name or "").strip()
            if plat and label:
                store_name_by_id[bsid] = f"{plat.upper()} {label}"
            elif label:
                store_name_by_id[bsid] = label

    linhas: list[dict[str, Any]] = []
    for o in orders:
        try:
            bsid = int(o.loja) if o.loja else None
        except (TypeError, ValueError):
            bsid = None
        # Corte só interessa em pedido ainda não enviado — igual à tela.
        corte = ""
        if o.marketplace_ship_deadline and o.situacao in _SITUACAO_NAO_VERDE:
            dl = o.marketplace_ship_deadline.astimezone(_BRT)
            corte = f"corte {dl.strftime('%d/%m %H:%M')}"
        linhas.append({
            "data_envio": o.em_andamento_data.isoformat() if o.em_andamento_data else "",
            "loja": (store_name_by_id.get(bsid) if bsid is not None else None) or (o.loja or ""),
            "corte": corte,
            "pedido_bling": o.numero or "",
            "pedido_marketplace": o.numeroloja or "",
            "cliente": o.nome_destinatario or "",
            "sku": o.item_codigo or "",
            "quantidade": o.item_quantidade or 1,
            "produto": o.item_descricao or "",
        })
    return linhas


@router.post("/pedidos/etiquetas")
async def get_pedidos_etiquetas_lote(
    payload: EtiquetasLoteIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("controle_estoque", "view"))],
) -> Response:
    """Junta as etiquetas dos pedidos selecionados num PDF só (impressão em lote).

    A ordem segue a da seleção que veio da tela. Pedidos sem etiqueta são
    IGNORADOS (o operador seleciona um bloco e imprime o que está pronto) —
    404 só quando nenhum dos selecionados tem etiqueta. Carimba `impressa_em`
    nos que entraram no PDF, pra tela marcar "Impressa" e evitar duplicidade.
    """
    # dict.fromkeys: dedupe preservando a ordem da seleção.
    pedidos = [p for p in dict.fromkeys(payload.pedidos) if p]
    rows = (
        await session.execute(
            select(NfEtiquetaArquivo).where(
                NfEtiquetaArquivo.pedido_bling.in_(pedidos)
            )
        )
    ).scalars().all()
    por_pedido = {r.pedido_bling: r for r in rows if r.blob}
    ordenados = [por_pedido[p] for p in pedidos if p in por_pedido]
    if not ordenados:
        raise HTTPException(status_code=404, detail="nf_etiqueta_nao_encontrada")

    try:
        partes = [_pdf_para_impressao(r) for r in ordenados]
        if payload.incluir_relatorio:
            impressos = [r.pedido_bling for r in ordenados]
            linhas = await _linhas_relatorio(session, impressos)
            if linhas:
                hoje = datetime.now(_BRT).strftime("%d/%m/%Y")
                partes.append(relatorio_pedidos_pdf(
                    linhas,
                    f"Relatório de pedidos — {hoje} — "
                    f"{len(impressos)} pedido(s), {len(linhas)} item(ns)",
                ))
        conteudo = juntar_varios(partes)
    except EtiquetaJuntarError as exc:
        logger.warning("nf_etiqueta_lote_falhou", erro=str(exc))
        raise HTTPException(status_code=422, detail="nf_etiqueta_lote_invalido") from exc

    agora = datetime.now(UTC)
    for r in ordenados:
        # Só a PRIMEIRA impressão carimba — reimprimir não reescreve a data,
        # senão o operador perde a referência de quando aquilo já saiu.
        if r.impressa_em is None:
            r.impressa_em = agora
    await session.commit()

    return Response(
        content=conteudo,
        media_type="application/pdf",
        headers={
            "Content-Disposition": 'inline; filename="etiquetas_lote.pdf"',
            "Cache-Control": "no-store, must-revalidate",
            "X-Etiquetas-Total": str(len(ordenados)),
            "Access-Control-Expose-Headers": "X-Etiquetas-Total",
        },
    )


@router.get("/pedidos/{pedido_bling}/etiqueta")
async def get_pedido_etiqueta(
    pedido_bling: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("controle_estoque", "view"))],
) -> Response:
    """Serve a etiqueta transformada do pedido (blob em nf_etiqueta_arquivo).

    Autenticado por cookie — o botão "Imprimir Etiqueta" abre a URL numa aba
    nova e o browser manda o cookie sozinho. 404 se ainda não há etiqueta
    (a etapa de visualização da NF automática não rodou pra este pedido)."""
    row = (
        await session.execute(
            select(NfEtiquetaArquivo).where(
                NfEtiquetaArquivo.pedido_bling == pedido_bling
            )
        )
    ).scalar_one_or_none()
    if row is None or not row.blob:
        # Sem etiqueta ainda (a NF pode ter chegado antes, mas não há o que
        # imprimir sem a etiqueta que cola no volume).
        raise HTTPException(status_code=404, detail="nf_etiqueta_nao_encontrada")
    conteudo = _pdf_para_impressao(row)
    if row.impressa_em is None:
        # Abrir a etiqueta é imprimir — carimba pra tela marcar "Impressa"
        # (mesma regra do lote: só a primeira vez).
        row.impressa_em = datetime.now(UTC)
        await session.commit()
    return Response(
        content=conteudo,
        media_type=row.content_type or "application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{row.filename}"',
            # Sem cache: a etiqueta é REGRAVADA (recaptura, regra de visualização
            # nova) sob a mesma URL — cachear serviria a versão velha por horas.
            "Cache-Control": "no-store, must-revalidate",
        },
    )


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

    # Contagem de envios pelo ledger de eventos (oficial desde o cutover):
    # conta o EVENTO de entrada na situação 15, bucketizado por `shipping_day`
    # (corte 10:00 BRT — migrations 0156/0158), imune ao recarimbo de
    # `em_andamento_data`. Tag resolvida via `sql_clause_for_tag`.
    ledger_where: list = [
        BlingEnvioEvento.shipping_day >= data_inicio,
        BlingEnvioEvento.shipping_day <= data_fim,
    ]
    if tags is not None:
        ledger_where.append(
            or_(*[_sql_clause_for_tag(BlingEnvioEvento.item_codigo, t) for t in tags])
        )
    ledger_rows = (
        await session.execute(
            select(
                BlingEnvioEvento.shipping_day.label("dia"),
                func.count(func.distinct(BlingEnvioEvento.bling_id)).label("envios"),
            )
            .where(and_(*ledger_where))
            .group_by(BlingEnvioEvento.shipping_day)
        )
    ).all()
    ledger_by_day = {r.dia.isoformat(): int(r.envios or 0) for r in ledger_rows if r.dia}

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

    # Per-day stock conferência aggregate. O denominador (total de
    # produtos) é calculado POR DIA — um produto só conta a partir do
    # seu dia de criação (BRT), senão produto novo no Bling regride dias
    # já conferidos pra 'parcial'. Conferidos vem por reference_date a
    # partir da seção 'estoque'. O front exibe Total/Parcial/Não conferido.
    denom_by_day = await _active_products_denominator_by_day(
        session, tags, data_inicio, data_fim,
    )
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

    locks_str = {d.isoformat() for d in locks}
    all_days = sorted(ledger_by_day, reverse=True)

    items: list[dict[str, Any]] = []
    total_envios = 0
    total_conferido = 0
    for dia_str in all_days:
        # `envios` = contagem oficial pelo ledger de evento (shipping_day,
        # corte 10:00 — migrations 0156/0158).
        envios_n = ledger_by_day.get(dia_str, 0)
        conf = checks.get(dia_str, False)
        # `conferido_filter` is applied client-of-the-loop so totals
        # reflect the visible set only. `all` (or None) shows everything;
        # the other two narrow to one bucket.
        if conferido_filter == "conferidos" and not conf:
            continue
        if conferido_filter == "nao_conferidos" and conf:
            continue
        estoque_conferidos = estoque_checks_by_day.get(dia_str, 0)
        # Denominador do DIA: só produtos criados até esse dia.
        denom_dia = denom_by_day.get(dia_str, 0)
        if dia_str in locks_str:
            # Dia travado: ignora o count de produtos.
            conferencia_estoque = "total"
        elif denom_dia == 0:
            conferencia_estoque = "nenhuma"
        elif estoque_conferidos >= denom_dia:
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
        *_active_simple_product_clauses(),
        Product.sku.notlike("%+%"),
        *_baseline_sku_exclusions(Product.sku),
    ]
    if tags is not None:
        where.append(or_(*[_sql_clause_for_tag(Product.sku, t) for t in tags]))
    stock_clause = _stock_filter_clause(estoque_filter)
    if stock_clause is not None:
        where.append(stock_clause)
    # DISTINCT sku: a conferência é keyed por SKU (StockCheck.reference_id =
    # SKU), então o denominador tem que contar SKUs, não linhas de Product.
    # SKUs duplicados (2 produtos com o mesmo SKU) inflavam o total e o dia
    # nunca fechava 100% mesmo com todos os checks ticados.
    n = (
        await session.execute(
            select(func.count(func.distinct(Product.sku)))
            .select_from(Product)
            .where(and_(*where))
        )
    ).scalar_one()
    return int(n or 0)


async def _active_products_denominator_by_day(
    session: AsyncSession,
    tags: list[str] | None,
    data_inicio: date,
    data_fim: date,
    estoque_filter: str | None = None,
) -> dict[str, int]:
    """Denominador de conferência POR DIA. Um produto só entra no
    denominador a partir do seu dia de CRIAÇÃO (BRT). Sem isso, um
    produto novo no Bling inflava o total de TODOS os dias anteriores
    (o denominador era o count atual, fixo), fazendo dias já conferidos
    regredirem de 'total' pra 'parcial'. Produtos criados depois de um
    dia não contam naquele dia — só do dia da criação pra frente.

    Retorna {iso_day: denominador_acumulado} pra cada dia do período.
    Mesmo filtro de situacao/formato/SKU+/baseline/tag do
    _count_active_products, pra ficar 1:1 com o numerador."""
    where: list = [
        *_active_simple_product_clauses(),
        Product.sku.notlike("%+%"),
        *_baseline_sku_exclusions(Product.sku),
    ]
    if tags is not None:
        where.append(or_(*[_sql_clause_for_tag(Product.sku, t) for t in tags]))
    stock_clause = _stock_filter_clause(estoque_filter)
    if stock_clause is not None:
        where.append(stock_clause)

    # created_at é timestamptz (UTC); converte pra BRT antes de extrair a
    # data, igual o resto do app (AT TIME ZONE 'America/Sao_Paulo').
    created_brt = func.date(func.timezone("America/Sao_Paulo", Product.created_at))

    # Dedupe por SKU: a conferência é keyed por SKU (StockCheck.reference_id),
    # então o denominador conta cada SKU UMA vez — no dia de criação do seu
    # PRIMEIRO produto (min). Sem isso, SKUs duplicados (2 linhas de Product
    # com o mesmo SKU) inflavam o total e o dia ficava "parcial" pra sempre,
    # mesmo com todos os checks ticados na aba Estoque.
    first_seen = (
        select(
            Product.sku.label("sku"),
            func.min(created_brt).label("d"),
        )
        .where(and_(*where))
        .group_by(Product.sku)
        .subquery()
    )

    # SKUs vistos ANTES do início do período: contam em todos os dias.
    baseline = (
        await session.execute(
            select(func.count())
            .select_from(first_seen)
            .where(first_seen.c.d < data_inicio)
        )
    ).scalar_one()

    # SKUs vistos pela 1ª vez DENTRO do período, agrupados pelo dia (BRT).
    rows = (
        await session.execute(
            select(first_seen.c.d.label("d"), func.count().label("n"))
            .where(first_seen.c.d >= data_inicio, first_seen.c.d <= data_fim)
            .group_by(first_seen.c.d)
        )
    ).all()
    per_create: dict[str, int] = {
        r.d.isoformat(): int(r.n or 0) for r in rows if r.d is not None
    }

    # Denominador acumulado dia a dia. Criados DEPOIS de data_fim ficam de
    # fora (não entram em nenhum dia visível), o que é o comportamento certo.
    result: dict[str, int] = {}
    running = int(baseline or 0)
    d = data_inicio
    while d <= data_fim:
        iso = d.isoformat()
        running += per_create.get(iso, 0)
        result[iso] = running
        d += timedelta(days=1)
    return result


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

    # Conta SKUs DISTINTOS por dia (reference_id = SKU) — 1:1 com o
    # denominador, que também dedupa por SKU. Admin agrega vários usuários
    # (evita duplicar quando 2 operadores conferem o mesmo SKU no mesmo dia);
    # non-admin normalmente já tem 1 check por SKU, mas o distinct blinda
    # contra qualquer linha duplicada.
    n_expr = func.count(func.distinct(StockCheck.reference_id))
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

    # Congela o saldo no ato da conferência da aba Estoque: products.stock /
    # reserved_stock são AO VIVO, então sem isto um dia passado mostraria o
    # saldo de hoje. Gravamos o saldo do instante do ✓ (reference_id = SKU).
    # Ao destickar (conferido=False), limpamos → volta ao comportamento ao
    # vivo. Só section='estoque' (pedido/envio não têm coluna de saldo).
    frozen_virtual: int | None = None
    frozen_reserved: int | None = None
    if section == "estoque" and conferido:
        prod = (
            await session.execute(
                select(Product.stock, Product.reserved_stock).where(
                    Product.sku == reference_id
                )
            )
        ).first()
        if prod is not None:
            frozen_virtual = int(prod.stock or 0)
            frozen_reserved = int(prod.reserved_stock or 0)

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
                saldo_virtual=frozen_virtual,
                reserved=frozen_reserved,
            )
        )
    else:
        existing.conferido = conferido
        if observacao is not None:
            existing.observacao = observacao or None
        if section == "estoque":
            # Recongela ao (re)confirmar; limpa (None) ao destickar.
            existing.saldo_virtual = frozen_virtual
            existing.reserved = frozen_reserved

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

    from app.services.devolution_stock_return import _get_bling_client

    tags = _resolve_tags(user, tag)

    where: list = [
        *_active_simple_product_clauses(),
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

    client = await _get_bling_client(session)
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

    from app.services.devolution_stock_return import _get_bling_client

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

        client = await _get_bling_client(session)
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
