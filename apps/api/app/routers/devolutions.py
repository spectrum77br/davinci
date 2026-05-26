from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.deps.auth import require_permission
from app.models import Devolution, Refund, User
from app.schemas.devolutions import (
    DevolutionCreate,
    DevolutionLookupOut,
    DevolutionOut,
    DevolutionPage,
    DevolutionPatch,
)

logger = structlog.get_logger()
router = APIRouter(prefix="/api/devolutions", tags=["devolutions"])

_REFUND_CONDICOES = {"Extraviado", "Manutenção"}


def _maybe_create_refund(session: AsyncSession, row: Devolution, condicao: str) -> None:
    prejuizo = (
        row.custo_produto or 0
        if condicao == "Extraviado"
        else row.custo_manutencao or 0
    )
    refund = Refund(
        data=row.data,
        pedido_bling=row.pedido_bling,
        pedido_marketplace=row.pedido_marketplace,
        conta=row.conta,
        tipo=condicao,
        prejuizo=prejuizo,
        reembolso=0,
    )
    session.add(refund)
    logger.info("refund_auto_created", pedido_bling=row.pedido_bling, tipo=condicao)
settings = get_settings()
SCHEMA = settings.database_schema


def _search_clause(search: str):
    q = f"%{search}%"
    return or_(
        Devolution.pedido_bling.ilike(q),
        Devolution.pedido_marketplace.ilike(q),
        Devolution.conta.ilike(q),
        Devolution.sku.ilike(q),
        Devolution.produtos.ilike(q),
        Devolution.condicao_produto.ilike(q),
        Devolution.motivo_devolucao.ilike(q),
        Devolution.tecnico.ilike(q),
        Devolution.observacao.ilike(q),
    )


@router.get("", response_model=DevolutionPage)
async def list_devolutions(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("devolucoes", "view"))],
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    search: str | None = Query(None),
    reembolso: bool | None = Query(None),
) -> DevolutionPage:
    where = []
    if search and search.strip():
        where.append(_search_clause(search.strip()))
    if reembolso is not None:
        where.append(Devolution.reembolso.is_(reembolso))

    stmt = (
        select(Devolution)
        .where(*where)
        .order_by(desc(Devolution.data).nulls_last(), desc(Devolution.created_at))
        .limit(limit)
        .offset(offset)
    )
    count_stmt = select(func.count()).select_from(Devolution).where(*where)

    items = (await session.execute(stmt)).scalars().all()
    total = (await session.execute(count_stmt)).scalar_one()

    return DevolutionPage(
        items=[DevolutionOut.model_validate(item) for item in items],
        total=int(total or 0),
        limit=limit,
        offset=offset,
    )


@router.get("/order-lookup", response_model=list[DevolutionLookupOut])
async def lookup_devolution_order(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("devolucoes", "view"))],
    pedido: str = Query(..., min_length=1),
) -> list[DevolutionLookupOut]:
    pedido = pedido.strip()
    if not pedido:
        raise HTTPException(422, detail={"code": "pedido_required"})

    q_like = f"%{pedido}%"
    rows = (
        await session.execute(
            text(
                f"""
                SELECT
                    v.data,
                    v.pedido_bling::text AS pedido_bling,
                    v.pedido_marketplace::text AS pedido_marketplace,
                    btrim(v.loja_nome) AS conta,
                    v.sku,
                    v.produto AS produtos,
                    1 AS quantidade,
                    COALESCE(bo.preco_custo::numeric, 0)::double precision AS custo_produto,
                    v.nome_destinatario,
                    v.cep_destino,
                    v.endereco_destino,
                    v.numero_destino,
                    v.complemento_destino,
                    v.bairro_destino,
                    v.cidade_destino,
                    v.uf_destino
                FROM "{SCHEMA}".vw_devolucoes v
                LEFT JOIN "{SCHEMA}".bling_orders bo ON bo.id = v.bling_order_item_id
                CROSS JOIN generate_series(1, GREATEST(1, COALESCE(v.quantidade::int, 1))) gs(unit_num)
                WHERE (
                    v.pedido_bling::text = :pedido
                    OR v.pedido_marketplace::text = :pedido
                    OR v.nome_destinatario ILIKE :q_like
                    OR v.cep_destino ILIKE :q_like
                )
                  AND v.loja_nome IS NOT NULL
                  AND btrim(v.loja_nome) <> ''
                ORDER BY v.data DESC NULLS LAST, v.pedido_bling, v.sku, gs.unit_num
                LIMIT 50
                """  # noqa: S608
            ),
            {"pedido": pedido, "q_like": q_like},
        )
    ).mappings().all()

    return [DevolutionLookupOut.model_validate(dict(row)) for row in rows]


@router.post("", response_model=DevolutionOut, status_code=status.HTTP_201_CREATED)
async def create_devolution(
    body: DevolutionCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("devolucoes", "edit"))],
) -> DevolutionOut:
    row = Devolution(
        data=body.data,
        pedido_bling=body.pedido_bling,
        pedido_marketplace=body.pedido_marketplace,
        conta=body.conta,
        sku=body.sku,
        produtos=body.produtos,
        custo_produto=body.custo_produto,
        condicao_produto=body.condicao_produto,
        link_abertura=body.link_abertura,
        reembolso=body.reembolso,
        motivo_devolucao=body.motivo_devolucao,
        custo_manutencao=body.custo_manutencao,
        tecnico=body.tecnico,
        devolver_estoque=body.devolver_estoque,
        observacao=body.observacao,
    )
    session.add(row)
    if body.condicao_produto in _REFUND_CONDICOES:
        _maybe_create_refund(session, row, body.condicao_produto)
    await session.commit()
    await session.refresh(row)
    logger.info("devolution_created", id=str(row.id), pedido_bling=row.pedido_bling)
    return DevolutionOut.model_validate(row)


@router.patch("/{devolution_id}", response_model=DevolutionOut)
async def patch_devolution(
    devolution_id: UUID,
    body: DevolutionPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("devolucoes", "edit"))],
) -> DevolutionOut:
    row = (
        await session.execute(select(Devolution).where(Devolution.id == devolution_id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail={"code": "devolution_not_found"})

    data = body.model_dump(exclude_unset=True)
    if data.get("conta") is None and "conta" in data:
        raise HTTPException(422, detail={"code": "conta_required"})

    prev_condicao = row.condicao_produto
    for key, value in data.items():
        setattr(row, key, value)

    new_condicao = row.condicao_produto
    if new_condicao in _REFUND_CONDICOES and new_condicao != prev_condicao:
        _maybe_create_refund(session, row, new_condicao)

    await session.commit()
    await session.refresh(row)
    return DevolutionOut.model_validate(row)


@router.delete("/{devolution_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_devolution(
    devolution_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("devolucoes", "delete"))],
) -> None:
    row = (
        await session.execute(select(Devolution).where(Devolution.id == devolution_id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail={"code": "devolution_not_found"})
    await session.delete(row)
    await session.commit()
    logger.info("devolution_deleted", id=str(devolution_id))
    return None
