from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.deps.auth import require_permission
from app.models import BlingOrder, Devolution, Refund, User
from app.schemas.devolutions import (
    BlingStockResultOut,
    DevolutionCreate,
    DevolutionLookupOut,
    DevolutionOut,
    DevolutionPage,
    DevolutionPatch,
)
from app.services.devolution_stock_return import (
    _STOCK_CONDICOES,
    return_product_to_bling_stock,
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
    out = DevolutionOut.model_validate(row)
    if body.condicao_produto in _STOCK_CONDICOES and body.devolver_estoque:
        sr = await return_product_to_bling_stock(session, row, body.condicao_produto)
        if sr is not None:
            out.bling_stock_result = BlingStockResultOut(**sr)
    return out


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
    prev_devolver_estoque = row.devolver_estoque
    for key, value in data.items():
        setattr(row, key, value)

    new_condicao = row.condicao_produto
    if new_condicao in _REFUND_CONDICOES and new_condicao != prev_condicao:
        _maybe_create_refund(session, row, new_condicao)

    await session.commit()
    await session.refresh(row)
    out = DevolutionOut.model_validate(row)
    condicao_changed = new_condicao in _STOCK_CONDICOES and new_condicao != prev_condicao
    toggle_turned_on = row.devolver_estoque and not prev_devolver_estoque
    if row.devolver_estoque and new_condicao in _STOCK_CONDICOES and (condicao_changed or toggle_turned_on):
        sr = await return_product_to_bling_stock(session, row, new_condicao)
        if sr is not None:
            out.bling_stock_result = BlingStockResultOut(**sr)
    return out


@router.post("/backfill-addresses", status_code=status.HTTP_200_OK)
async def backfill_addresses(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("devolucoes", "edit"))],
    limit: int = Query(50, ge=1, le=200),
) -> dict:
    """Re-fetch delivery addresses from Bling for devolution orders that have
    NULL nome_destinatario. Calls GET /pedidos/vendas/{bling_id} individually.
    Returns counts of processed / updated / failed records.
    """
    from app.services.devolution_stock_return import _get_bling_client

    _SITUACOES = ("83957", "83960", "83961", "83966")

    # Find orders that need backfill
    rows = (
        await session.execute(
            select(BlingOrder.id, BlingOrder.bling_id, BlingOrder.numero)
            .where(
                BlingOrder.situacao.in_(_SITUACOES),
                BlingOrder.nome_destinatario.is_(None),
                BlingOrder.bling_id.is_not(None),
            )
            .order_by(desc(BlingOrder.data))
            .limit(limit)
        )
    ).all()

    if not rows:
        return {"processed": 0, "updated": 0, "failed": 0, "message": "Nenhum pedido sem endereço encontrado"}

    client = await _get_bling_client(session)
    if client is None:
        raise HTTPException(503, detail={"code": "no_bling_integration"})

    processed = updated = failed = 0

    for row_id, bling_id, numero in rows:
        processed += 1
        try:
            raw = await client.get_order(bling_id)
            if not raw:
                failed += 1
                continue

            # Extract address using same logic as bling_orders.py
            transporte = raw.get("transporte") or {}
            transporte = transporte if isinstance(transporte, dict) else {}
            t_contato = transporte.get("contato") or {}
            t_contato = t_contato if isinstance(t_contato, dict) else {}
            t_endereco = transporte.get("enderecoEntrega") or {}
            t_endereco = t_endereco if isinstance(t_endereco, dict) else {}
            buyer = raw.get("contato") or {}
            buyer = buyer if isinstance(buyer, dict) else {}
            buyer_end = buyer.get("endereco") or {}
            buyer_end = buyer_end if isinstance(buyer_end, dict) else {}

            def _v(tp_f: str, buyer_f: str | None = None) -> str | None:
                return t_endereco.get(tp_f) or buyer_end.get(buyer_f or tp_f) or None

            values: dict = {}
            nome = t_contato.get("nome") or buyer.get("nome") or None
            if nome:
                values["nome_destinatario"] = nome
            for col, tp_f in [
                ("cep_destino", "cep"),
                ("endereco_destino", "endereco"),
                ("numero_destino", "numero"),
                ("complemento_destino", "complemento"),
                ("bairro_destino", "bairro"),
                ("cidade_destino", "municipio"),
                ("uf_destino", "uf"),
            ]:
                val = _v(tp_f)
                if val:
                    values[col] = val

            if values:
                await session.execute(
                    update(BlingOrder).where(BlingOrder.id == row_id).values(**values)
                )
                updated += 1
                logger.info("devolution_address_backfill_ok", numero=numero, fields=list(values))
            else:
                logger.info("devolution_address_backfill_no_data", numero=numero)

        except Exception as exc:  # noqa: BLE001
            failed += 1
            logger.warning("devolution_address_backfill_error", numero=numero, error=str(exc))

    await session.commit()
    logger.info("devolution_address_backfill_done", processed=processed, updated=updated, failed=failed)
    return {
        "processed": processed,
        "updated": updated,
        "failed": failed,
        "message": f"{updated} de {processed} pedidos atualizados com endereço",
    }


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
