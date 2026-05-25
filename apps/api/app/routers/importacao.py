"""Importação — controle de pedidos de importação de malas.

Endpoints behind the `importacao` permission. All data is org-wide
(no user_id / company_id scoping — same pattern as financeiro).

Computed fields (not stored): the GET /products endpoint enriches each
row with memoria_consumo, reposicao_estoque, saldo_reposicao based on
the manual fields (estoque_bling, consumo_diario, maior_media_30d) +
the config singleton. GET /lotes enriches with previsto, saldo, prazo.

Auto-trigger: when PATCH /lotes/{id} sets `fechamento` from NULL to a
date, the router inserts a row into import_resumo with the lote's
saldo. The operator can also create resumo entries manually (e.g.
devolution adjustments).
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import and_, desc, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_permission
from app.models import (
    ImportConfig,
    ImportLote,
    ImportLoteItem,
    ImportProduct,
    ImportResumo,
    Product,
    User,
)
from app.schemas.importacao import (
    ImportConfigOut,
    ImportConfigPatch,
    ImportLoteCreate,
    ImportLoteItemUpsert,
    ImportLoteOut,
    ImportLotePatch,
    ImportProductCreate,
    ImportProductOut,
    ImportProductPatch,
    ImportResumoCreate,
    ImportResumoList,
    ImportResumoOut,
)

logger = structlog.get_logger()
router = APIRouter(prefix="/api/importacao", tags=["importacao"])

_ZERO = Decimal("0")


# ── Config singleton ─────────────────────────────────────────────────


@router.get("/config", response_model=ImportConfigOut)
async def get_config(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("importacao", "view"))],
) -> ImportConfigOut:
    row = await session.get(ImportConfig, 1)
    if row is None:
        row = ImportConfig(id=1, tempo_reposicao=150, tempo_estoque=60)
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return ImportConfigOut.model_validate(row, from_attributes=True)


@router.patch("/config", response_model=ImportConfigOut)
async def patch_config(
    body: ImportConfigPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("importacao", "edit"))],
) -> ImportConfigOut:
    row = await session.get(ImportConfig, 1)
    if row is None:
        row = ImportConfig(id=1)
        session.add(row)
    for k, v in body.model_dump(exclude_unset=True).items():
        if v is not None:
            setattr(row, k, v)
    await session.commit()
    await session.refresh(row)
    return ImportConfigOut.model_validate(row, from_attributes=True)


# ── Products ─────────────────────────────────────────────────────────


def _compute_product_fields(
    p: ImportProduct,
    cfg: ImportConfig,
    pedidos_em_aberto: int,
) -> tuple[Decimal | None, int | None, int | None]:
    """Returns (memoria_consumo, reposicao_estoque, saldo_reposicao).

    Mirrors the spec:
      * memoria_consumo = max(consumo_diario, maior_media_30d) — but
        if estoque=0, pin to maior_media_30d so we don't divide by zero
        consumption.
      * reposicao_estoque = (dias_necessarios − duração_estoque) * consumo_diario
      * saldo_reposicao = reposicao_estoque − pedidos_em_aberto
    """
    consumo = Decimal(p.consumo_diario) if p.consumo_diario is not None else None
    media = Decimal(p.maior_media_30d) if p.maior_media_30d is not None else None
    estoque = p.estoque_bling

    # memoria_consumo
    if estoque == 0:
        memoria = media
    elif consumo is not None and media is not None:
        memoria = max(consumo, media)
    elif consumo is not None:
        memoria = consumo
    else:
        memoria = media

    if memoria is None or memoria <= 0 or estoque is None or consumo is None:
        return memoria, None, None

    duracao = Decimal(estoque) / memoria  # days the current stock lasts
    necessario = Decimal(cfg.tempo_reposicao + cfg.tempo_estoque)
    saldo_dias = necessario - duracao
    reposicao_dec = saldo_dias * consumo
    reposicao = int(reposicao_dec)
    saldo = reposicao - pedidos_em_aberto
    return memoria, reposicao, saldo


class _Effective:
    """Lightweight stand-in for _compute_product_fields when we want to
    feed it auto-pulled values without mutating the ORM row."""
    __slots__ = ("estoque_bling", "consumo_diario", "maior_media_30d")

    def __init__(self, estoque, consumo, media):
        self.estoque_bling = estoque
        self.consumo_diario = consumo
        self.maior_media_30d = media


@router.get("/products", response_model=list[ImportProductOut])
async def list_products(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("importacao", "view"))],
) -> list[ImportProductOut]:
    products = (
        await session.execute(
            select(ImportProduct).order_by(ImportProduct.sku)
        )
    ).scalars().all()

    cfg = await session.get(ImportConfig, 1)
    if cfg is None:
        cfg = ImportConfig(id=1, tempo_reposicao=150, tempo_estoque=60)

    # Open-lote items: aggregate for pedidos_em_aberto + per-(lote,product) map.
    open_items = (
        await session.execute(
            select(ImportLoteItem, ImportLote)
            .join(ImportLote, ImportLote.id == ImportLoteItem.lote_id)
        )
    ).all()
    qty_by_product_open: dict[UUID, int] = {}
    qty_by_pair: dict[UUID, dict[str, int]] = {}
    for item, lote in open_items:
        qty_by_pair.setdefault(item.product_id, {})[str(item.lote_id)] = item.quantidade
        if lote.fechamento is None:
            qty_by_product_open[item.product_id] = (
                qty_by_product_open.get(item.product_id, 0) + (item.quantidade or 0)
            )

    # ── Auto-pull: estoque_bling from products.stock by SKU ───────────
    skus_lower = [(p.sku or "").lower() for p in products if p.sku]
    stock_by_sku: dict[str, int | None] = {}
    if skus_lower:
        stock_rows = (await session.execute(
            select(func.lower(Product.sku).label("sku"), Product.stock)
            .where(func.lower(Product.sku).in_(skus_lower))
        )).all()
        stock_by_sku = {r.sku: r.stock for r in stock_rows if r.sku}

    # ── Auto-pull: 6 monthly sales buckets (30, 60, 90, 120, 150, 180d)
    # from bling_orders by item_codigo. consumo_diario = bucket30 / 30;
    # maior_media_30d = MAX(buckets) / 30. Excluded situacoes match the
    # pricing/sales-map endpoint so the two pages agree on what counts.
    sales_by_sku: dict[str, dict[str, int]] = {}
    if skus_lower:
        sales_rows = (await session.execute(text("""
            SELECT
                LOWER(item_codigo) AS sku,
                SUM(CASE WHEN data >= now() - interval '30 days'  THEN COALESCE(item_quantidade,0) ELSE 0 END) AS b30,
                SUM(CASE WHEN data >= now() - interval '60 days'  AND data < now() - interval '30 days'  THEN COALESCE(item_quantidade,0) ELSE 0 END) AS b60,
                SUM(CASE WHEN data >= now() - interval '90 days'  AND data < now() - interval '60 days'  THEN COALESCE(item_quantidade,0) ELSE 0 END) AS b90,
                SUM(CASE WHEN data >= now() - interval '120 days' AND data < now() - interval '90 days'  THEN COALESCE(item_quantidade,0) ELSE 0 END) AS b120,
                SUM(CASE WHEN data >= now() - interval '150 days' AND data < now() - interval '120 days' THEN COALESCE(item_quantidade,0) ELSE 0 END) AS b150,
                SUM(CASE WHEN data >= now() - interval '180 days' AND data < now() - interval '150 days' THEN COALESCE(item_quantidade,0) ELSE 0 END) AS b180
            FROM davinci.bling_orders
            WHERE item_codigo IS NOT NULL AND item_codigo <> ''
              AND data >= now() - interval '180 days'
              AND COALESCE(situacao, '') NOT IN ('Cancelado', 'Devolvido', '12')
              AND LOWER(item_codigo) = ANY(:skus)
            GROUP BY LOWER(item_codigo)
        """), {"skus": skus_lower})).all()
        sales_by_sku = {
            r.sku: {
                "b30": int(r.b30 or 0), "b60": int(r.b60 or 0),
                "b90": int(r.b90 or 0), "b120": int(r.b120 or 0),
                "b150": int(r.b150 or 0), "b180": int(r.b180 or 0),
            }
            for r in sales_rows
        }

    out: list[ImportProductOut] = []
    for p in products:
        sku_lc = (p.sku or "").lower()

        # Effective stock: auto-pulled wins; fall back to stored manual value.
        auto_stock = stock_by_sku.get(sku_lc)
        eff_estoque: int | None
        if auto_stock is not None:
            eff_estoque = int(auto_stock)
        else:
            eff_estoque = p.estoque_bling

        # Effective consumo + maior_media from sales buckets when present.
        auto_sales = sales_by_sku.get(sku_lc)
        if auto_sales:
            eff_consumo = Decimal(auto_sales["b30"]) / Decimal(30)
            biggest = max(
                auto_sales["b30"], auto_sales["b60"], auto_sales["b90"],
                auto_sales["b120"], auto_sales["b150"], auto_sales["b180"],
            )
            eff_media = Decimal(biggest) / Decimal(30)
        else:
            eff_consumo = (
                Decimal(p.consumo_diario) if p.consumo_diario is not None else None
            )
            eff_media = (
                Decimal(p.maior_media_30d) if p.maior_media_30d is not None else None
            )

        pedidos = qty_by_product_open.get(p.id, 0)
        memoria, reposicao, saldo = _compute_product_fields(
            _Effective(eff_estoque, eff_consumo, eff_media), cfg, pedidos,
        )
        out.append(ImportProductOut(
            id=p.id,
            fornecedor=p.fornecedor, modelo_china=p.modelo_china, cor_china=p.cor_china,
            fechamento=p.fechamento, tsa=p.tsa, modelo_bling=p.modelo_bling,
            sku=p.sku, cor=p.cor, custo_bling=p.custo_bling,
            estoque_bling=eff_estoque,
            consumo_diario=eff_consumo,
            maior_media_30d=eff_media,
            obs=p.obs,
            memoria_consumo=memoria,
            reposicao_estoque=reposicao,
            saldo_reposicao=saldo,
            lote_quantidades=qty_by_pair.get(p.id, {}),
            created_at=p.created_at, updated_at=p.updated_at,
        ))
    return out


@router.post("/products", response_model=ImportProductOut, status_code=status.HTTP_201_CREATED)
async def create_product(
    body: ImportProductCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("importacao", "edit"))],
) -> ImportProductOut:
    row = ImportProduct(**body.model_dump())
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return ImportProductOut(
        id=row.id,
        fornecedor=row.fornecedor, modelo_china=row.modelo_china, cor_china=row.cor_china,
        fechamento=row.fechamento, tsa=row.tsa, modelo_bling=row.modelo_bling,
        sku=row.sku, cor=row.cor, custo_bling=row.custo_bling,
        estoque_bling=row.estoque_bling, consumo_diario=row.consumo_diario,
        maior_media_30d=row.maior_media_30d, obs=row.obs,
        memoria_consumo=None, reposicao_estoque=None, saldo_reposicao=None,
        lote_quantidades={},
        created_at=row.created_at, updated_at=row.updated_at,
    )


@router.patch("/products/{row_id}", response_model=ImportProductOut)
async def patch_product(
    row_id: UUID,
    body: ImportProductPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("importacao", "edit"))],
) -> ImportProductOut:
    row = await session.get(ImportProduct, row_id)
    if row is None:
        raise HTTPException(404, detail={"code": "product_not_found"})
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(row, k, v)
    await session.commit()
    await session.refresh(row)
    return ImportProductOut(
        id=row.id,
        fornecedor=row.fornecedor, modelo_china=row.modelo_china, cor_china=row.cor_china,
        fechamento=row.fechamento, tsa=row.tsa, modelo_bling=row.modelo_bling,
        sku=row.sku, cor=row.cor, custo_bling=row.custo_bling,
        estoque_bling=row.estoque_bling, consumo_diario=row.consumo_diario,
        maior_media_30d=row.maior_media_30d, obs=row.obs,
        memoria_consumo=None, reposicao_estoque=None, saldo_reposicao=None,
        lote_quantidades={},
        created_at=row.created_at, updated_at=row.updated_at,
    )


@router.delete("/products/{row_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product(
    row_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("importacao", "delete"))],
) -> None:
    row = await session.get(ImportProduct, row_id)
    if row is None:
        raise HTTPException(404, detail={"code": "product_not_found"})
    await session.delete(row)
    await session.commit()


# ── Lotes ────────────────────────────────────────────────────────────


async def _enrich_lote(
    session: AsyncSession,
    lote: ImportLote,
) -> ImportLoteOut:
    """Computes previsto = SUM(quant × custo) + saldo + prazo."""
    previsto_row = await session.execute(
        select(
            func.coalesce(
                func.sum(ImportLoteItem.quantidade * ImportProduct.custo_bling),
                0,
            )
        )
        .select_from(ImportLoteItem)
        .join(ImportProduct, ImportProduct.id == ImportLoteItem.product_id)
        .where(ImportLoteItem.lote_id == lote.id)
    )
    previsto = Decimal(previsto_row.scalar() or 0)
    realizado = Decimal(lote.realizado or 0)
    saldo = previsto - realizado
    prazo = (lote.fechamento - lote.abertura).days if lote.fechamento else None
    return ImportLoteOut(
        id=lote.id, nome=lote.nome, abertura=lote.abertura,
        fechamento=lote.fechamento, realizado=realizado,
        previsto=previsto, saldo=saldo, prazo=prazo,
        is_aberto=lote.fechamento is None,
    )


@router.get("/lotes", response_model=list[ImportLoteOut])
async def list_lotes(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("importacao", "view"))],
) -> list[ImportLoteOut]:
    lotes = (
        await session.execute(
            select(ImportLote).order_by(ImportLote.abertura.desc(), ImportLote.nome)
        )
    ).scalars().all()
    return [await _enrich_lote(session, lt) for lt in lotes]


@router.post("/lotes", response_model=ImportLoteOut, status_code=status.HTTP_201_CREATED)
async def create_lote(
    body: ImportLoteCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("importacao", "edit"))],
) -> ImportLoteOut:
    row = ImportLote(nome=body.nome, abertura=body.abertura)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return await _enrich_lote(session, row)


@router.patch("/lotes/{lote_id}", response_model=ImportLoteOut)
async def patch_lote(
    lote_id: UUID,
    body: ImportLotePatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("importacao", "edit"))],
) -> ImportLoteOut:
    row = await session.get(ImportLote, lote_id)
    if row is None:
        raise HTTPException(404, detail={"code": "lote_not_found"})

    # Capture fechamento transition for the auto-resumo trigger.
    was_open = row.fechamento is None
    data = body.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(row, k, v)
    await session.commit()
    await session.refresh(row)

    # If this PATCH transitioned the lote from open → closed, auto-create
    # the resumo entry with the lote's current saldo. The operator can
    # still adjust by adding manual entries later.
    if was_open and row.fechamento is not None:
        enriched = await _enrich_lote(session, row)
        resumo_row = ImportResumo(
            data=row.fechamento,
            lote_id=row.id,
            lote_nome=row.nome,
            saldo=enriched.saldo,
        )
        session.add(resumo_row)
        await session.commit()
        logger.info(
            "importacao_lote_auto_resumo",
            lote_id=str(row.id), nome=row.nome, saldo=str(enriched.saldo),
        )

    return await _enrich_lote(session, row)


@router.delete("/lotes/{lote_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_lote(
    lote_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("importacao", "delete"))],
) -> None:
    row = await session.get(ImportLote, lote_id)
    if row is None:
        raise HTTPException(404, detail={"code": "lote_not_found"})
    await session.delete(row)
    await session.commit()


# ── LoteItem (upsert per cell) ─────────────────────────────────────


@router.put("/lotes/{lote_id}/items", status_code=status.HTTP_200_OK)
async def upsert_lote_item(
    lote_id: UUID,
    body: ImportLoteItemUpsert,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("importacao", "edit"))],
) -> dict:
    """Upsert quantity for (lote, product). Quantity 0 deletes the row."""
    lote = await session.get(ImportLote, lote_id)
    if lote is None:
        raise HTTPException(404, detail={"code": "lote_not_found"})
    prod = await session.get(ImportProduct, body.product_id)
    if prod is None:
        raise HTTPException(404, detail={"code": "product_not_found"})

    existing = (
        await session.execute(
            select(ImportLoteItem).where(
                and_(
                    ImportLoteItem.lote_id == lote_id,
                    ImportLoteItem.product_id == body.product_id,
                )
            )
        )
    ).scalar_one_or_none()

    if body.quantidade == 0:
        if existing is not None:
            await session.delete(existing)
            await session.commit()
        return {"ok": True, "quantidade": 0}

    if existing is None:
        session.add(ImportLoteItem(
            lote_id=lote_id, product_id=body.product_id, quantidade=body.quantidade,
        ))
    else:
        existing.quantidade = body.quantidade
    await session.commit()
    return {"ok": True, "quantidade": body.quantidade}


# ── Resumo (lançamentos financeiros) ──────────────────────────────


@router.get("/resumo", response_model=ImportResumoList)
async def list_resumo(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("importacao", "view"))],
) -> ImportResumoList:
    rows = (
        await session.execute(
            # ImportResumo is append-only and has no created_at — sort
            # by (data, id) so rows with the same data stay in insert
            # order (UUID v4 isn't time-ordered but the secondary sort
            # at least makes the response stable across calls).
            select(ImportResumo).order_by(ImportResumo.data, ImportResumo.id)
        )
    ).scalars().all()
    total = sum((Decimal(r.saldo) for r in rows), _ZERO)
    return ImportResumoList(
        items=[ImportResumoOut.model_validate(r, from_attributes=True) for r in rows],
        total=total,
    )


@router.post("/resumo", response_model=ImportResumoOut, status_code=status.HTTP_201_CREATED)
async def create_resumo(
    body: ImportResumoCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("importacao", "edit"))],
) -> ImportResumoOut:
    row = ImportResumo(**body.model_dump())
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return ImportResumoOut.model_validate(row, from_attributes=True)


@router.delete("/resumo/{row_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_resumo(
    row_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("importacao", "delete"))],
) -> None:
    row = await session.get(ImportResumo, row_id)
    if row is None:
        raise HTTPException(404, detail={"code": "resumo_not_found"})
    await session.delete(row)
    await session.commit()
