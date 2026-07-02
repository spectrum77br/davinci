from datetime import UTC, date, datetime, time, timedelta
from io import BytesIO
from typing import Annotated
from uuid import UUID
from zoneinfo import ZoneInfo

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from sqlalchemy import Text, cast, desc, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.deps.auth import require_permission
from app.models import BlingOrder, Refund, SituacaoBling, User
from app.schemas.refunds import (
    RefundCreate,
    RefundLookupOut,
    RefundLookupPage,
    RefundOrderCostOut,
    RefundOut,
    RefundPage,
    RefundPatch,
    _clamp_cliente_reembolso,
)
from app.services.reembolso_sync import sync_reembolso_for_pedido
from app.services.verificar_margem import (
    refresh_for_pedido as _refresh_verificar_margem_for_pedido,
)

logger = structlog.get_logger()
router = APIRouter(prefix="/api/refunds", tags=["refunds"])
settings = get_settings()
SCHEMA = settings.database_schema
SAO_PAULO = ZoneInfo("America/Sao_Paulo")


def _ident(name: str) -> str:
    return f'"{name.replace(chr(34), chr(34) + chr(34))}"'


def _qualified_table(name: str) -> str:
    return f"{_ident(SCHEMA)}.{_ident(name)}"


def _search_clause(search: str):
    q = f"%{search}%"
    return (
        or_(
            Refund.pedido_bling.ilike(q),
            Refund.pedido_marketplace.ilike(q),
            Refund.plataforma.ilike(q),
            Refund.conta.ilike(q),
            Refund.tipo.ilike(q),
            Refund.chamado.ilike(q),
            Refund.chamado_url.ilike(q),
            Refund.operacao.ilike(q),
            Refund.observacao.ilike(q),
        ),
        q,
    )


async def _situacoes_by_pedido(
    session: AsyncSession, pedidos: set[str]
) -> dict[str, str]:
    """Situacao Bling ATUAL por numero de pedido (nome via situacao_bling;
    fallback no id cru quando o catalogo nao tem a situacao)."""
    if not pedidos:
        return {}
    rows = await session.execute(
        select(
            BlingOrder.numero,
            func.max(func.coalesce(SituacaoBling.nome, BlingOrder.situacao)),
        )
        .join(
            SituacaoBling,
            cast(SituacaoBling.id, Text) == BlingOrder.situacao,
            isouter=True,
        )
        .where(BlingOrder.numero.in_(pedidos))
        .group_by(BlingOrder.numero)
    )
    return {str(numero): nome for numero, nome in rows.all() if numero and nome}


async def _sync_reembolso_to_bling_orders(
    session: AsyncSession, pedido_bling: str | None
) -> None:
    """Replica os refunds conferidos do pedido em `bling_orders.reembolso`.

    Delega para o serviço compartilhado `sync_reembolso_for_pedido` (mesma
    lógica usada pelo ingest de pedidos). NÃO faz commit — o caller controla a
    transação."""
    await sync_reembolso_for_pedido(session, pedido_bling)


async def _refresh_margem_snapshot(
    session: AsyncSession, pedido_bling: str | None
) -> None:
    """Reconstrói a linha do pedido no snapshot `verificar_margem` (lê da view
    `_all`, sem janela de 20d) para que a página de Margem reflita o reembolso
    recém-gravado em `bling_orders`. Tolerante a falha: o reembolso já está
    commitado; um erro/lentidão no refresh não pode derrubar o save — o cron de
    30 min e o botão "atualizar" são fallbacks. Deve ser chamado APÓS o commit
    que persiste `bling_orders.reembolso`."""
    pedido = (pedido_bling or "").strip()
    if not pedido:
        return
    try:
        await _refresh_verificar_margem_for_pedido(session, pedido)
    except Exception:  # noqa: BLE001
        logger.warning("refund_margem_snapshot_refresh_failed", pedido_bling=pedido)


def _build_where(
    search: str | None,
    platform: str | None,
    tipo: str | None,
    conferido: bool | None,
    data_inicio: date | None = None,
    data_fim: date | None = None,
) -> list:
    where = []
    if search and search.strip():
        clause, _q = _search_clause(search.strip())
        where.append(clause)
    if platform:
        where.append(Refund.plataforma == platform)
    if tipo:
        where.append(Refund.tipo == tipo)
    if conferido is not None:
        where.append(Refund.conferido.is_(conferido))
    # Filtro por "data do conferido" (Refund.conferido_at), exposto só a admins
    # na UI. Período inclusivo [data_inicio 00:00, data_fim 23:59:59] no fuso de
    # SP. Refunds não conferidos (conferido_at NULL) ficam de fora quando há filtro.
    if data_inicio is not None:
        start = datetime.combine(data_inicio, time.min, tzinfo=SAO_PAULO)
        where.append(Refund.conferido_at >= start.astimezone(UTC))
    if data_fim is not None:
        end = datetime.combine(data_fim, time.min, tzinfo=SAO_PAULO) + timedelta(days=1)
        where.append(Refund.conferido_at < end.astimezone(UTC))
    return where


@router.get("", response_model=RefundPage)
async def list_refunds(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("reembolso", "view"))],
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    search: str | None = Query(None),
    platform: str | None = Query(None),
    tipo: str | None = Query(None),
    conferido: bool | None = Query(None),
    data_inicio: date | None = Query(None),
    data_fim: date | None = Query(None),
) -> RefundPage:
    where = _build_where(search, platform, tipo, conferido, data_inicio, data_fim)

    stmt = (
        select(Refund)
        .where(*where)
        .order_by(desc(Refund.data).nulls_last(), desc(Refund.created_at))
        .limit(limit)
        .offset(offset)
    )
    count_stmt = select(func.count()).select_from(Refund).where(*where)
    # Totais do conjunto filtrado INTEIRO (todas as páginas), não só os `items`
    # carregados — assim o rodapé bate com a linha Reembolso do quadro Operacional.
    summary_stmt = select(
        func.coalesce(func.sum(Refund.prejuizo), 0.0),
        func.coalesce(func.sum(Refund.reembolso), 0.0),
        func.count().filter(Refund.conferido.is_(False)),
    ).where(*where)
    platforms_stmt = (
        select(Refund.plataforma)
        .where(Refund.plataforma.is_not(None))
        .distinct()
        .order_by(Refund.plataforma)
    )

    items = (await session.execute(stmt)).scalars().all()
    total = (await session.execute(count_stmt)).scalar_one()
    sum_prejuizo, sum_reembolso, n_a_conferir = (await session.execute(summary_stmt)).one()
    platforms = [p for p in (await session.execute(platforms_stmt)).scalars().all() if p]

    situacoes = await _situacoes_by_pedido(
        session, {item.pedido_bling for item in items if item.pedido_bling}
    )
    out_items = []
    for item in items:
        out = RefundOut.model_validate(item)
        if item.pedido_bling:
            out.situacao_bling = situacoes.get(item.pedido_bling)
        out_items.append(out)

    return RefundPage(
        items=out_items,
        total=int(total or 0),
        limit=limit,
        offset=offset,
        platforms=platforms,
        total_prejuizo=round(float(sum_prejuizo or 0), 2),
        total_reembolso=round(float(sum_reembolso or 0), 2),
        total_a_conferir=int(n_a_conferir or 0),
    )


def _fmt_dt_sp(dt: datetime | None) -> str:
    """Datetime → 'dd/mm/aaaa HH:MM' no fuso de São Paulo (vazio se None)."""
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(SAO_PAULO).strftime("%d/%m/%Y %H:%M")


_EXPORT_COLUMNS: list[tuple[str, str]] = [
    ("Data", "data"),
    ("Pedido Bling", "pedido_bling"),
    ("Pedido Marketplace", "pedido_marketplace"),
    ("Plataforma", "plataforma"),
    ("Conta", "conta"),
    ("Tipo", "tipo"),
    ("Prejuízo", "prejuizo"),
    ("Reembolso", "reembolso"),
    ("Chamado", "chamado"),
    ("Link chamado", "chamado_url"),
    ("Chamado resolvido", "chamado_resolvido"),
    ("Operação", "operacao"),
    ("Conferido", "conferido"),
    ("Situação Bling", "situacao_bling"),
    ("Observação", "observacao"),
    ("Criado em", "created_at"),
]


@router.get("/export.xlsx")
async def export_refunds(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("reembolso", "view"))],
    search: str | None = Query(None),
    platform: str | None = Query(None),
    tipo: str | None = Query(None),
    conferido: bool | None = Query(None),
    data_inicio: date | None = Query(None),
    data_fim: date | None = Query(None),
) -> StreamingResponse:
    """Exporta os reembolsos (com os mesmos filtros da lista) em XLSX."""
    where = _build_where(search, platform, tipo, conferido, data_inicio, data_fim)
    rows = (
        await session.execute(
            select(Refund)
            .where(*where)
            .order_by(desc(Refund.data).nulls_last(), desc(Refund.created_at))
        )
    ).scalars().all()

    situacoes = await _situacoes_by_pedido(
        session, {r.pedido_bling for r in rows if r.pedido_bling}
    )

    wb = Workbook()
    ws = wb.active
    ws.title = "Reembolsos"
    ws.append([label for label, _ in _EXPORT_COLUMNS])

    for r in rows:
        line = []
        for _, field in _EXPORT_COLUMNS:
            if field == "situacao_bling":
                line.append(situacoes.get(r.pedido_bling or "", ""))
            elif field in ("data", "created_at"):
                line.append(_fmt_dt_sp(getattr(r, field, None)))
            elif field in ("conferido", "chamado_resolvido"):
                line.append("Sim" if getattr(r, field, False) else "Não")
            else:
                value = getattr(r, field, None)
                line.append(value if value is not None else "")
        ws.append(line)

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    stamp = datetime.now(SAO_PAULO).strftime("%Y%m%d_%H%M")
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="reembolsos_{stamp}.xlsx"'},
    )


def _lookup_refund_sql(view_name: str) -> str:
    return f"""
        SELECT
            MAX(v.data) AS data,
            v.pedido_bling::text AS pedido_bling,
            MAX(v.pedido_marketplace)::text AS pedido_marketplace,
            COALESCE(v.plataforma_bling, v.plataforma_financeiro)::text AS plataforma,
            btrim(v.loja_nome) AS conta,
            SUM(v.bling_custo_produtos)::double precision AS custo_produto,
            MAX(d.custo_manutencao)::double precision AS custo_manutencao
        FROM {_qualified_table(view_name)} v
        LEFT JOIN {_qualified_table("devolutions")} d
            ON d.pedido_bling = v.pedido_bling::text
            AND btrim(d.conta) = btrim(v.loja_nome)
        WHERE (
            v.pedido_bling::text = :pedido
            OR v.pedido_marketplace::text = :pedido
            OR (
                CAST(:pedido_bling AS text) IS NOT NULL
                AND v.pedido_bling::text = CAST(:pedido_bling AS text)
            )
        )
          AND v.loja_nome IS NOT NULL
          AND btrim(v.loja_nome) <> ''
        GROUP BY
            v.pedido_bling,
            COALESCE(v.plataforma_bling, v.plataforma_financeiro),
            btrim(v.loja_nome)
        ORDER BY MAX(v.data) DESC NULLS LAST
        LIMIT 20
        """  # noqa: S608


def _lookup_refund_all_sql() -> str:
    # force_refresh path (orders outside the default view's 20-day window).
    # Reads vw_bling_pedidos directly so the pedido predicate pushes down to an
    # index scan, instead of materializing the full margins view
    # (vw_conciliacao_margens_marketplace_all). That view has no date filter and
    # builds the entire pricing/freight/event machinery before the outer WHERE
    # can apply (>2 min -> proxy 502). The lookup only needs Bling-side columns,
    # which all come from vw_bling_pedidos plus the cheap financials lateral
    # (for the plataforma fallback). Output columns match _lookup_refund_sql.
    return f"""
        SELECT
            MAX(bp.data) AS data,
            bp.numero::text AS pedido_bling,
            MAX(bp.numeroloja)::text AS pedido_marketplace,
            COALESCE(bp.marketplace, f.platform::text)::text AS plataforma,
            btrim(bp.loja_nome) AS conta,
            SUM(bp.preco_custo * bp.item_quantidade::numeric)::double precision AS custo_produto,
            MAX(d.custo_manutencao)::double precision AS custo_manutencao
        FROM {_qualified_table("vw_bling_pedidos")} bp
        LEFT JOIN LATERAL (
            SELECT mf.platform
            FROM {_qualified_table("marketplace_order_financials")} mf
            WHERE mf.bling_id = bp.bling_id
              AND (bp.numeroloja IS NULL OR mf.external_order_id = bp.numeroloja)
            ORDER BY (CASE WHEN mf.external_order_id = bp.numeroloja THEN 0 ELSE 1 END),
                     mf.fetched_at DESC NULLS LAST, mf.created_at DESC
            LIMIT 1
        ) f ON true
        LEFT JOIN {_qualified_table("devolutions")} d
            ON d.pedido_bling = bp.numero::text
            AND btrim(d.conta) = btrim(bp.loja_nome)
        WHERE (
            bp.numero::text = :pedido
            OR bp.numeroloja::text = :pedido
            OR (
                CAST(:pedido_bling AS text) IS NOT NULL
                AND bp.numero::text = CAST(:pedido_bling AS text)
            )
        )
          AND bp.loja_nome IS NOT NULL
          AND btrim(bp.loja_nome) <> ''
        GROUP BY
            bp.numero,
            COALESCE(bp.marketplace, f.platform::text),
            btrim(bp.loja_nome)
        ORDER BY MAX(bp.data) DESC NULLS LAST
        LIMIT 20
        """  # noqa: S608


async def _find_bling_numero(session: AsyncSession, pedido: str) -> str | None:
    row = (
        await session.execute(
            text(
                f"""
                SELECT numero
                FROM {_qualified_table("bling_orders")}
                WHERE numero = :pedido OR numeroloja = :pedido
                ORDER BY data DESC NULLS LAST
                LIMIT 1
                """  # noqa: S608
            ),
            {"pedido": pedido},
        )
    ).first()
    return str(row[0]) if row and row[0] else None


@router.get("/order-lookup", response_model=RefundLookupPage)
async def lookup_refund_order(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("reembolso", "view"))],
    pedido: str = Query(..., min_length=1),
    force_refresh: bool = Query(False),
) -> RefundLookupPage:
    pedido = pedido.strip()
    if not pedido:
        raise HTTPException(422, detail={"code": "pedido_required"})

    params = {"pedido": pedido, "pedido_bling": None}

    async def _execute(sql: str) -> list:
        return list((await session.execute(text(sql), params)).mappings().all())

    rows = await _execute(_lookup_refund_sql("vw_conciliacao_margens_marketplace"))
    historico_disponivel = False

    if not rows:
        bling_numero = await _find_bling_numero(session, pedido)
        if bling_numero:
            if force_refresh:
                params["pedido_bling"] = bling_numero
                rows = await _execute(_lookup_refund_all_sql())
            else:
                historico_disponivel = True

    return RefundLookupPage(
        items=[RefundLookupOut.model_validate(dict(row)) for row in rows],
        historico_disponivel=historico_disponivel,
    )


@router.get("/order-cost", response_model=RefundOrderCostOut)
async def get_order_cost(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("reembolso", "view"))],
    pedido_bling: str = Query(..., min_length=1),
    conta: str = Query(..., min_length=1),
) -> RefundOrderCostOut:
    pedido_bling = pedido_bling.strip()
    conta = conta.strip()
    if not pedido_bling or not conta:
        raise HTTPException(422, detail={"code": "pedido_and_conta_required"})

    row = (
        await session.execute(
            text(
                f"""
                SELECT SUM(v.bling_custo_produtos)::double precision AS custo_produto
                FROM "{SCHEMA}".vw_conciliacao_margens_marketplace v
                WHERE v.pedido_bling::text = :pedido_bling
                  AND btrim(v.loja_nome) = :conta
                """  # noqa: S608
            ),
            {"pedido_bling": pedido_bling, "conta": conta},
        )
    ).mappings().first()

    return RefundOrderCostOut(
        pedido_bling=pedido_bling,
        conta=conta,
        custo_produto=row["custo_produto"] if row else None,
    )


@router.post("", response_model=RefundOut, status_code=status.HTTP_201_CREATED)
async def create_refund(
    body: RefundCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    u: Annotated[User, Depends(require_permission("reembolso", "edit"))],
) -> RefundOut:
    row = Refund(
        data=body.data,
        pedido_bling=body.pedido_bling,
        pedido_marketplace=body.pedido_marketplace,
        plataforma=body.plataforma,
        conta=body.conta,
        tipo=body.tipo,
        prejuizo=body.prejuizo,
        reembolso=body.reembolso,
        chamado=body.chamado,
        chamado_url=body.chamado_url,
        chamado_resolvido=body.chamado_resolvido,
        operacao=body.operacao,
        conferido=False,
        observacao=body.observacao,
        created_by=u.id,
    )
    session.add(row)
    await session.flush()
    await _sync_reembolso_to_bling_orders(session, row.pedido_bling)
    await session.commit()
    await _refresh_margem_snapshot(session, row.pedido_bling)
    await session.refresh(row)
    logger.info(
        "refund_created",
        id=str(row.id),
        pedido_bling=row.pedido_bling,
        created_by=str(u.id),
    )
    return RefundOut.model_validate(row)


@router.patch("/{refund_id}", response_model=RefundOut)
async def patch_refund(
    refund_id: UUID,
    body: RefundPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("reembolso", "edit"))],
) -> RefundOut:
    row = (await session.execute(select(Refund).where(Refund.id == refund_id))).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail={"code": "refund_not_found"})

    prev_pedido_bling = row.pedido_bling
    was_conferido = row.conferido

    data = body.model_dump(exclude_unset=True)
    if data.get("conta") is None and "conta" in data:
        raise HTTPException(422, detail={"code": "conta_required"})

    for key, value in data.items():
        setattr(row, key, value)

    # Carimba quando `conferido` transiciona (false->true grava agora; true->false
    # limpa). Única fonte da data usada no quadro "Operacional — 3 meses".
    if "conferido" in data and row.conferido != was_conferido:
        row.conferido_at = datetime.now(UTC) if row.conferido else None

    # Enforce Cliente -> reembolso <= 0 against the merged state (tipo or
    # reembolso may have come from either the patch or the existing row).
    row.reembolso = _clamp_cliente_reembolso(row.tipo, row.reembolso)

    await session.flush()
    # Ressincroniza o pedido atual; se o patch mudou o pedido, ressincroniza o
    # antigo também (perdeu este refund) antes de gravar.
    await _sync_reembolso_to_bling_orders(session, row.pedido_bling)
    if prev_pedido_bling and prev_pedido_bling != row.pedido_bling:
        await _sync_reembolso_to_bling_orders(session, prev_pedido_bling)

    await session.commit()
    await _refresh_margem_snapshot(session, row.pedido_bling)
    if prev_pedido_bling and prev_pedido_bling != row.pedido_bling:
        await _refresh_margem_snapshot(session, prev_pedido_bling)
    await session.refresh(row)
    return RefundOut.model_validate(row)


@router.delete("/{refund_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_refund(
    refund_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("reembolso", "delete"))],
) -> None:
    row = (await session.execute(select(Refund).where(Refund.id == refund_id))).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail={"code": "refund_not_found"})
    pedido_bling = row.pedido_bling
    await session.delete(row)
    await session.flush()
    await _sync_reembolso_to_bling_orders(session, pedido_bling)
    await session.commit()
    await _refresh_margem_snapshot(session, pedido_bling)
    logger.info("refund_deleted", id=str(refund_id))
    return None
