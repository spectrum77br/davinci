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
    RefundOrderCostOut,
    RefundOut,
    RefundPage,
    RefundPatch,
)

logger = structlog.get_logger()
router = APIRouter(prefix="/api/refunds", tags=["refunds"])
settings = get_settings()
SCHEMA = settings.database_schema


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


@router.get("/order-lookup", response_model=list[RefundLookupOut])
async def lookup_refund_order(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("reembolso", "view"))],
    pedido: str = Query(..., min_length=1),
) -> list[RefundLookupOut]:
    pedido = pedido.strip()
    if not pedido:
        raise HTTPException(422, detail={"code": "pedido_required"})

    rows = (
        await session.execute(
            text(
                f"""
                SELECT
                    MAX(v.data) AS data,
                    v.pedido_bling::text AS pedido_bling,
                    MAX(v.pedido_marketplace)::text AS pedido_marketplace,
                    COALESCE(v.plataforma_bling, v.plataforma_financeiro)::text AS plataforma,
                    btrim(v.loja_nome) AS conta,
                    SUM(v.bling_custo_produtos)::double precision AS custo_produto
                FROM "{SCHEMA}".vw_conciliacao_margens_marketplace v
                WHERE (v.pedido_bling::text = :pedido OR v.pedido_marketplace::text = :pedido)
                  AND v.loja_nome IS NOT NULL
                  AND btrim(v.loja_nome) <> ''
                GROUP BY
                    v.pedido_bling,
                    COALESCE(v.plataforma_bling, v.plataforma_financeiro),
                    btrim(v.loja_nome)
                ORDER BY MAX(v.data) DESC NULLS LAST
                LIMIT 20
                """  # noqa: S608
            ),
            {"pedido": pedido},
        )
    ).mappings().all()

    return [RefundLookupOut.model_validate(dict(row)) for row in rows]


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
    _u: Annotated[User, Depends(require_permission("reembolso", "edit"))],
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
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    logger.info("refund_created", id=str(row.id), pedido_bling=row.pedido_bling)
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
