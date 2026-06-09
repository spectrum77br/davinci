from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.deps.auth import require_permission
from app.models import Refund, User
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

logger = structlog.get_logger()
router = APIRouter(prefix="/api/refunds", tags=["refunds"])
settings = get_settings()
SCHEMA = settings.database_schema


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
            Refund.operacao.ilike(q),
            Refund.observacao.ilike(q),
        ),
        q,
    )


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
) -> RefundPage:
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

    stmt = (
        select(Refund)
        .where(*where)
        .order_by(desc(Refund.data).nulls_last(), desc(Refund.created_at))
        .limit(limit)
        .offset(offset)
    )
    count_stmt = select(func.count()).select_from(Refund).where(*where)
    platforms_stmt = (
        select(Refund.plataforma)
        .where(Refund.plataforma.is_not(None))
        .distinct()
        .order_by(Refund.plataforma)
    )

    items = (await session.execute(stmt)).scalars().all()
    total = (await session.execute(count_stmt)).scalar_one()
    platforms = [p for p in (await session.execute(platforms_stmt)).scalars().all() if p]

    return RefundPage(
        items=[RefundOut.model_validate(item) for item in items],
        total=int(total or 0),
        limit=limit,
        offset=offset,
        platforms=platforms,
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
        operacao=body.operacao,
        conferido=False,
        observacao=body.observacao,
        created_by=u.id,
    )
    session.add(row)
    await session.commit()
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

    data = body.model_dump(exclude_unset=True)
    if data.get("conta") is None and "conta" in data:
        raise HTTPException(422, detail={"code": "conta_required"})

    for key, value in data.items():
        setattr(row, key, value)

    # Enforce Cliente -> reembolso <= 0 against the merged state (tipo or
    # reembolso may have come from either the patch or the existing row).
    row.reembolso = _clamp_cliente_reembolso(row.tipo, row.reembolso)

    await session.commit()
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
    await session.delete(row)
    await session.commit()
    logger.info("refund_deleted", id=str(refund_id))
    return None
