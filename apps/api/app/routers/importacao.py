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

from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_permission
from app.models import (
    CotacaoFabricante,
    CotacaoProduto,
    CotacaoValor,
    ImportConfig,
    ImportCotacaoParams,
    ImportKitBase,
    ImportKitMark,
    ImportKitVariation,
    ImportLote,
    ImportLoteItem,
    ImportProduct,
    ImportResumo,
    Product,
    User,
)
from app.schemas.importacao import (
    CotacaoFabricanteOut,
    CotacaoFabricantePatch,
    CotacaoGridOut,
    CotacaoProdutoOut,
    CotacaoProdutoPatch,
    CotacaoValorOut,
    CotacaoValorUpsert,
    ImportConfigOut,
    ImportConfigPatch,
    ImportCotacaoParamsOut,
    ImportCotacaoParamsPatch,
    ImportFreteAjusteCreate,
    ImportFreteList,
    ImportFreteRow,
    ImportKitBaseOut,
    ImportKitGridOut,
    ImportKitMarkOut,
    ImportKitMarkToggle,
    ImportKitVariationOut,
    ImportLoteCreate,
    ImportLoteItemPatch,
    ImportLoteItemUpsert,
    ImportLoteOut,
    ImportLotePatch,
    ImportProductCotacaoPatch,
    ImportProductCreate,
    ImportProductOut,
    ImportProductPatch,
    ImportResumoCreate,
    ImportResumoList,
    ImportResumoOut,
    ImportResumoPatch,
)
from app.services.importacao_naming import generate_product_name
from app.services.pricing.audit import build_match_indexes, match_one_sku_to_keys
from app.worker_pool import get_arq_pool

logger = structlog.get_logger()
router = APIRouter(prefix="/api/importacao", tags=["importacao"])

_ZERO = Decimal("0")

# Selector top-level: cada categoria tem seus próprios dados. Filtro
# aplicado em todos os GETs; create grava a categoria recebida.
_CATEGORIAS = ("mala", "eletro", "celular")
_CategoriaQ = Annotated[str, Query(pattern="^(mala|eletro|celular)$")]


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


# ── Cotação params (aba Cotação do Celular, etapa 3) ──────────────


# Defaults validados pelo operador 2026-06-02 (mesma fonte do seed da
# migration 0119): câmbio R$ 5,10, frete regular 16%, swap 6%,
# acessórios 20%, adicional R$ 12. Usados quando o GET é chamado pra
# uma categoria sem row na tabela (auto-cria com esses valores).
_COTACAO_DEFAULTS: dict[str, Decimal] = {
    "taxa_cambio": Decimal("5.10"),
    "frete_regular_pct": Decimal("0.16"),
    "frete_swap_pct": Decimal("0.06"),
    "frete_acessorios_pct": Decimal("0.20"),
    "adicional": Decimal("12.00"),
}


def _validate_cotacao_params(body: ImportCotacaoParamsPatch) -> None:
    """Limites operacionais: percentuais em [0, 1], câmbio > 0,
    adicional >= 0. Reject cedo evita gravar valores inviáveis."""
    fields = body.model_dump(exclude_unset=True)
    if (v := fields.get("taxa_cambio")) is not None and Decimal(v) <= 0:
        raise HTTPException(422, detail={"code": "taxa_cambio_must_be_positive"})
    if (v := fields.get("adicional")) is not None and Decimal(v) < 0:
        raise HTTPException(422, detail={"code": "adicional_must_be_non_negative"})
    for pct_key in ("frete_regular_pct", "frete_swap_pct", "frete_acessorios_pct"):
        v = fields.get(pct_key)
        if v is not None and not (Decimal("0") <= Decimal(v) <= Decimal("1")):
            raise HTTPException(422, detail={"code": f"{pct_key}_out_of_range"})


@router.get("/cotacao/params", response_model=ImportCotacaoParamsOut)
async def get_cotacao_params(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("importacao", "view"))],
    categoria: _CategoriaQ = "celular",
) -> ImportCotacaoParamsOut:
    """Parâmetros globais da fórmula `previsto = usd*(1+frete)*câmbio + adic`.
    Auto-cria a row com defaults na primeira chamada por categoria."""
    row = (await session.execute(
        select(ImportCotacaoParams).where(ImportCotacaoParams.categoria == categoria)
    )).scalar_one_or_none()
    if row is None:
        row = ImportCotacaoParams(categoria=categoria, **_COTACAO_DEFAULTS)
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return ImportCotacaoParamsOut.model_validate(row, from_attributes=True)


@router.patch("/cotacao/params", response_model=ImportCotacaoParamsOut)
async def patch_cotacao_params(
    body: ImportCotacaoParamsPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("importacao", "edit"))],
    categoria: _CategoriaQ = "celular",
) -> ImportCotacaoParamsOut:
    _validate_cotacao_params(body)
    row = (await session.execute(
        select(ImportCotacaoParams).where(ImportCotacaoParams.categoria == categoria)
    )).scalar_one_or_none()
    if row is None:
        row = ImportCotacaoParams(categoria=categoria, **_COTACAO_DEFAULTS)
        session.add(row)
    for k, v in body.model_dump(exclude_unset=True).items():
        if v is not None:
            setattr(row, k, v)
    await session.commit()
    await session.refresh(row)
    return ImportCotacaoParamsOut.model_validate(row, from_attributes=True)


@router.patch("/cotacao/produto/{produto_id}", response_model=ImportProductOut)
async def patch_cotacao_import_product(
    produto_id: UUID,
    body: ImportProductCotacaoPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("importacao", "edit"))],
) -> ImportProductOut:
    """Atualiza SÓ os 3 campos da cotação (valor_usd, valor_brl_realizado,
    frete_type) de um produto. Separado do PATCH /products/{id} pra
    deixar autosave isolado (aba Importação não esmaga a aba Cotação
    e vice-versa)."""
    row = await session.get(ImportProduct, produto_id)
    if row is None:
        raise HTTPException(404, detail={"code": "import_product_not_found"})
    fields = body.model_dump(exclude_unset=True)
    if "frete_type" in fields and fields["frete_type"] not in ("regular", "swap", "acessorios"):
        raise HTTPException(422, detail={"code": "invalid_frete_type"})
    for k, v in fields.items():
        setattr(row, k, v)
    await session.commit()
    await session.refresh(row)
    # Frontend não usa estes campos computados aqui (a aba Cotação é
    # uma lista plana), mas o response_model exige; preenche default 0
    # pra não custar query extra.
    return ImportProductOut(
        id=row.id,
        categoria=row.categoria,
        fornecedor=row.fornecedor,
        modelo_china=row.modelo_china,
        cor_china=row.cor_china,
        fechamento=row.fechamento,
        tsa=row.tsa,
        modelo_bling=row.modelo_bling,
        sku=row.sku,
        cor=row.cor,
        custo_bling=row.custo_bling,
        estoque_bling=row.estoque_bling,
        consumo_diario=row.consumo_diario,
        maior_media_30d=row.maior_media_30d,
        obs=row.obs,
        valor_usd=row.valor_usd,
        valor_brl_realizado=row.valor_brl_realizado,
        frete_type=row.frete_type,
        nome_gerado=generate_product_name(row.categoria, row.modelo_bling, row.sku, row.cor),
        bling_sync_status=row.bling_sync_status,
        bling_sync_marked_at=row.bling_sync_marked_at,
        bling_product_id=row.bling_product_id,
        bling_sync_error=row.bling_sync_error,
        bling_sync_attempted_at=row.bling_sync_attempted_at,
        bling_sync_done_at=row.bling_sync_done_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


# ── Categoria counts (selector top-level) ───────────────────────────


@router.get("/categoria-counts")
async def categoria_counts(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("importacao", "view"))],
) -> dict[str, int]:
    """Contagem de import_products por categoria — alimenta o selector
    top-level (Mala (N) / Eletro (M) / Celular (K))."""
    rows = (await session.execute(
        select(ImportProduct.categoria, func.count()).group_by(ImportProduct.categoria)
    )).all()
    counts: dict[str, int] = dict.fromkeys(_CATEGORIAS, 0)
    for cat, n in rows:
        counts[cat] = int(n)
    return counts


# ── Products ─────────────────────────────────────────────────────────


def _compute_product_fields(
    p: ImportProduct,
    cfg: ImportConfig,
    pedidos_em_aberto: int,
) -> tuple[Decimal | None, int | None, int | None]:
    """Returns (memoria_consumo, reposicao_estoque, saldo_reposicao).

    Per operator spec — memoria is NOT a MAX, it's a gate on estoque:
      * estoque > 0  → memoria = consumo_diario (current rate is reliable)
      * estoque ≤ 0  → memoria = maior_media_30d (current rate is
                       distorted by stock-out; use the historical peak)

    Then:
      * reposicao_estoque = (dias_necessarios − duração_estoque) * consumo_diario
      * saldo_reposicao = reposicao_estoque − pedidos_em_aberto
    """
    consumo = Decimal(p.consumo_diario) if p.consumo_diario is not None else None
    media = Decimal(p.maior_media_30d) if p.maior_media_30d is not None else None
    estoque = p.estoque_bling

    # Gate on estoque, not MAX. When stock ran out the current 30-day
    # window includes the out-of-stock days, dragging consumo to ~0;
    # using maior_media_30d instead reflects real demand.
    if estoque is None or estoque <= 0:
        memoria = media
    else:
        memoria = consumo

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
    categoria: _CategoriaQ = "mala",
) -> list[ImportProductOut]:
    products = (
        await session.execute(
            select(ImportProduct)
            .where(ImportProduct.categoria == categoria)
            .order_by(ImportProduct.sku)
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
    #
    # Matching uses the same helper as /pricing/sales-map (mala dept):
    # exact match + size-overlap kit expansion. A piece SKU like
    # `b045.18` that never sold standalone still picks up every kit
    # b045.X.18.Y that does — each kit sale also consumes the
    # individual piece's stock for replenishment purposes.
    #
    # TODO(import-dept-resolution): all import_products today are mala/
    # acessórios, so hardcoding "mala" matches operator reality. When
    # other dept slugs land here (eletrônico etc), look the dept up via
    # pricing_products or an explicit segment_id column on import_products.
    sales_by_key: dict[str, dict[str, int]] = {}
    candidates_by_sku: dict[str, set[str]] = {}
    if skus_lower:
        # 1) All distinct item_codigo keys present in bling_orders within
        # the 180d window — fed into the matcher to build the indexes.
        all_keys_rows = (await session.execute(text("""
            SELECT DISTINCT LOWER(TRIM(item_codigo)) AS k
            FROM davinci.bling_orders
            WHERE item_codigo IS NOT NULL AND item_codigo <> ''
              AND data >= now() - interval '180 days'
              AND COALESCE(situacao, '') NOT IN ('Cancelado', 'Devolvido', '12')
        """))).all()
        all_keys = [r.k for r in all_keys_rows if r.k]
        by_exact, by_base_celular, by_base_sizes = build_match_indexes(all_keys)

        # 2) For each import SKU, get the candidate item_codigo set via
        # the shared rules (dept=mala for now — see TODO above).
        for sku_lc in skus_lower:
            candidates_by_sku[sku_lc] = match_one_sku_to_keys(
                sku_lc, "mala", by_exact, by_base_celular, by_base_sizes,
            )

        # 3) One aggregated query over the UNION of all candidate keys.
        all_candidates = set().union(*candidates_by_sku.values()) if candidates_by_sku else set()
        if all_candidates:
            sales_rows = (await session.execute(text("""
                SELECT
                    LOWER(TRIM(item_codigo)) AS sku,
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
                  AND LOWER(TRIM(item_codigo)) = ANY(:keys)
                GROUP BY 1
            """), {"keys": list(all_candidates)})).all()
            sales_by_key = {
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
        # Sum every candidate key's bucket — for mala dept this includes
        # exact + kit superset matches per match_one_sku_to_keys.
        candidates = candidates_by_sku.get(sku_lc, set())
        auto_sales: dict[str, int] | None = None
        if candidates:
            agg = dict.fromkeys(("b30", "b60", "b90", "b120", "b150", "b180"), 0)
            for k in candidates:
                buckets = sales_by_key.get(k)
                if not buckets:
                    continue
                for b in agg:
                    agg[b] += buckets[b]
            if any(v > 0 for v in agg.values()):
                auto_sales = agg
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
            categoria=p.categoria,
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
            nome_gerado=generate_product_name(p.categoria, p.modelo_bling, p.sku, p.cor),
            bling_sync_status=p.bling_sync_status,
            bling_sync_marked_at=p.bling_sync_marked_at,
            bling_product_id=p.bling_product_id,
            bling_sync_error=p.bling_sync_error,
            bling_sync_attempted_at=p.bling_sync_attempted_at,
            bling_sync_done_at=p.bling_sync_done_at,
            valor_usd=p.valor_usd,
            valor_brl_realizado=p.valor_brl_realizado,
            frete_type=p.frete_type or "regular",
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
        categoria=row.categoria,
        fornecedor=row.fornecedor, modelo_china=row.modelo_china, cor_china=row.cor_china,
        fechamento=row.fechamento, tsa=row.tsa, modelo_bling=row.modelo_bling,
        sku=row.sku, cor=row.cor, custo_bling=row.custo_bling,
        estoque_bling=row.estoque_bling, consumo_diario=row.consumo_diario,
        maior_media_30d=row.maior_media_30d, obs=row.obs,
        memoria_consumo=None, reposicao_estoque=None, saldo_reposicao=None,
        nome_gerado=generate_product_name(row.categoria, row.modelo_bling, row.sku, row.cor),
        bling_sync_status=row.bling_sync_status,
        bling_sync_marked_at=row.bling_sync_marked_at,
        bling_product_id=row.bling_product_id,
        bling_sync_error=row.bling_sync_error,
        bling_sync_attempted_at=row.bling_sync_attempted_at,
        bling_sync_done_at=row.bling_sync_done_at,
        valor_usd=row.valor_usd,
        valor_brl_realizado=row.valor_brl_realizado,
        frete_type=row.frete_type or "regular",
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
        categoria=row.categoria,
        fornecedor=row.fornecedor, modelo_china=row.modelo_china, cor_china=row.cor_china,
        fechamento=row.fechamento, tsa=row.tsa, modelo_bling=row.modelo_bling,
        sku=row.sku, cor=row.cor, custo_bling=row.custo_bling,
        estoque_bling=row.estoque_bling, consumo_diario=row.consumo_diario,
        maior_media_30d=row.maior_media_30d, obs=row.obs,
        memoria_consumo=None, reposicao_estoque=None, saldo_reposicao=None,
        nome_gerado=generate_product_name(row.categoria, row.modelo_bling, row.sku, row.cor),
        bling_sync_status=row.bling_sync_status,
        bling_sync_marked_at=row.bling_sync_marked_at,
        bling_product_id=row.bling_product_id,
        bling_sync_error=row.bling_sync_error,
        bling_sync_attempted_at=row.bling_sync_attempted_at,
        bling_sync_done_at=row.bling_sync_done_at,
        valor_usd=row.valor_usd,
        valor_brl_realizado=row.valor_brl_realizado,
        frete_type=row.frete_type or "regular",
        lote_quantidades={},
        created_at=row.created_at, updated_at=row.updated_at,
    )


@router.post(
    "/products/{row_id}/sync-bling",
    response_model=ImportProductOut,
    status_code=status.HTTP_200_OK,
)
async def sync_product_to_bling(
    row_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("importacao", "edit"))],
) -> ImportProductOut:
    """Enfileira criação real do produto no Bling. Worker em
    `app.services.import_product_bling_create`:
      * nome:      generate_mala_name(modelo_bling, sku, cor)
      * categoria: 'mala' (via find_or_create_category)
      * custo:     row.custo_bling (preço de venda NÃO é enviado —
                   operador define no Bling)
      * formato:   'S' (simples)
    Após sucesso, cria Product local linkado pelo bling_product_id
    pra que kits possam usar como componente.

    Idempotente: worker pula se bling_product_id já preenchido.
    """
    row = await session.get(ImportProduct, row_id)
    if row is None:
        raise HTTPException(404, detail={"code": "product_not_found"})

    # Reseta estado de erro anterior (operador pode clicar de novo
    # após corrigir SKU inválido, falta de integration, etc).
    now = datetime.now(UTC)
    row.bling_sync_status = "pending"
    row.bling_sync_marked_at = now
    row.bling_sync_attempted_at = now
    row.bling_sync_error = None
    await session.commit()
    await session.refresh(row)

    try:
        pool = await get_arq_pool()
        await pool.enqueue_job("sync_import_product_to_bling_job", str(row.id))
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "import_product_enqueue_failed",
            product_id=str(row.id), err=str(e)[:200],
        )

    nome = generate_product_name(row.categoria, row.modelo_bling, row.sku, row.cor)
    return ImportProductOut(
        id=row.id,
        categoria=row.categoria,
        fornecedor=row.fornecedor, modelo_china=row.modelo_china, cor_china=row.cor_china,
        fechamento=row.fechamento, tsa=row.tsa, modelo_bling=row.modelo_bling,
        sku=row.sku, cor=row.cor, custo_bling=row.custo_bling,
        estoque_bling=row.estoque_bling, consumo_diario=row.consumo_diario,
        maior_media_30d=row.maior_media_30d, obs=row.obs,
        memoria_consumo=None, reposicao_estoque=None, saldo_reposicao=None,
        nome_gerado=nome,
        bling_sync_status=row.bling_sync_status,
        bling_sync_marked_at=row.bling_sync_marked_at,
        bling_product_id=row.bling_product_id,
        bling_sync_error=row.bling_sync_error,
        bling_sync_attempted_at=row.bling_sync_attempted_at,
        bling_sync_done_at=row.bling_sync_done_at,
        valor_usd=row.valor_usd,
        valor_brl_realizado=row.valor_brl_realizado,
        frete_type=row.frete_type or "regular",
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
    """Enrichment do lote pra resposta da UI. Convenções por categoria:

    `previsto`:
      * Se `lote.previsto_manual` setado (não null) → usa override.
        Celular usa isso pelo header de lote ativo (operador edita).
      * Senão → computed via SUM(quantidade × custo_bling) dos items.
        Mala usa isso (comportamento original preservado).

    `realizado`:
      * Mala/Eletro → lê `lote.realizado` (coluna gravada, operador edita).
      * Celular → computed via SUM(custoBRL(valor_usd) × estoque_bling)
        usando ImportCotacaoParams da categoria. Operador edita
        valor_usd por produto (aba Cotação); `realizado` é derivado.

    `saldo` = previsto - realizado. `prazo` = (fechamento - abertura).days.
    """
    is_celular = (lote.categoria or "").lower() == "celular"

    # previsto: override (manual) > computed (SUM qty × custo_bling).
    if lote.previsto_manual is not None:
        previsto = Decimal(lote.previsto_manual)
    else:
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

    # realizado: branch por categoria.
    if is_celular:
        # Pra celular: SUM(custoBRL × estoque) — custoBRL =
        # valor_usd × câmbio × (1 + frete_regular_pct) + adicional.
        # Adicional aplica por linha (não somatório separado) — coerente
        # com a aba Cotação. estoque NULL → 0 (linha não contribui).
        cot = (await session.execute(
            select(ImportCotacaoParams).where(ImportCotacaoParams.categoria == "celular")
        )).scalar_one_or_none()
        cambio = Decimal(cot.taxa_cambio) if cot else Decimal("5.10")
        frete = Decimal(cot.frete_regular_pct) if cot else Decimal("0.16")
        adic = Decimal(cot.adicional) if cot else Decimal("12.00")
        realizado_row = await session.execute(
            select(ImportProduct.valor_usd, ImportProduct.estoque_bling)
            .select_from(ImportLoteItem)
            .join(ImportProduct, ImportProduct.id == ImportLoteItem.product_id)
            .where(ImportLoteItem.lote_id == lote.id)
        )
        realizado = Decimal("0")
        for usd, est in realizado_row.all():
            if usd is None or est is None or Decimal(usd) <= 0 or int(est) <= 0:
                continue
            custo_unit = Decimal(usd) * cambio * (Decimal("1") + frete) + adic
            realizado += custo_unit * Decimal(int(est))
        realizado = realizado.quantize(Decimal("0.01"))
    else:
        realizado = Decimal(lote.realizado or 0)

    saldo = previsto - realizado
    prazo = (lote.fechamento - lote.abertura).days if lote.fechamento else None
    return ImportLoteOut(
        id=lote.id, categoria=lote.categoria, nome=lote.nome, abertura=lote.abertura,
        fechamento=lote.fechamento, realizado=realizado,
        transportadora=lote.transportadora, obs=lote.obs,
        previsto_manual=lote.previsto_manual,
        previsto=previsto, saldo=saldo, prazo=prazo,
        is_aberto=lote.fechamento is None,
    )


@router.get("/lote-ativo", response_model=ImportLoteOut | None)
async def get_lote_ativo(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("importacao", "view"))],
    categoria: _CategoriaQ = "celular",
) -> ImportLoteOut | None:
    """Lote ativo = mais recente sem fechamento. Usado pelo header da
    aba Importação Celular como resumo. Retorna null se não houver
    lote aberto pra essa categoria — frontend mostra "Nenhum lote
    ativo" e botão pra criar."""
    lote = (
        await session.execute(
            select(ImportLote)
            .where(
                ImportLote.categoria == categoria,
                ImportLote.fechamento.is_(None),
            )
            .order_by(ImportLote.abertura.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if lote is None:
        return None
    return await _enrich_lote(session, lote)


@router.get("/lotes", response_model=list[ImportLoteOut])
async def list_lotes(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("importacao", "view"))],
    categoria: _CategoriaQ = "mala",
) -> list[ImportLoteOut]:
    lotes = (
        await session.execute(
            # Ordem natural por nome (ML25, ML26, ML27…) — o operador
            # nomeia em sequência crescente. Ordenar por abertura.desc
            # quebrava a sequência quando um lote novo recebia data
            # mais recente que os anteriores.
            select(ImportLote)
            .where(ImportLote.categoria == categoria)
            .order_by(ImportLote.nome)
        )
    ).scalars().all()
    return [await _enrich_lote(session, lt) for lt in lotes]


@router.post("/lotes", response_model=ImportLoteOut, status_code=status.HTTP_201_CREATED)
async def create_lote(
    body: ImportLoteCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("importacao", "edit"))],
) -> ImportLoteOut:
    row = ImportLote(nome=body.nome, abertura=body.abertura, categoria=body.categoria)
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
            categoria=row.categoria,
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
    categoria: _CategoriaQ = "mala",
) -> ImportResumoList:
    rows = (
        await session.execute(
            # ImportResumo is append-only and has no created_at — sort
            # by (data, id) so rows with the same data stay in insert
            # order (UUID v4 isn't time-ordered but the secondary sort
            # at least makes the response stable across calls).
            select(ImportResumo)
            .where(ImportResumo.categoria == categoria)
            .order_by(ImportResumo.data, ImportResumo.id)
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


@router.patch("/resumo/{row_id}", response_model=ImportResumoOut)
async def patch_resumo(
    row_id: UUID,
    body: ImportResumoPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("importacao", "edit"))],
) -> ImportResumoOut:
    """Atualiza `obs` de uma linha do Resumo (operador anota motivo de
    ajuste, etc.). Demais campos imutáveis pós-create."""
    row = await session.get(ImportResumo, row_id)
    if row is None:
        raise HTTPException(404, detail={"code": "resumo_not_found"})
    for k, v in body.model_dump(exclude_unset=True).items():
        if v is not None:
            setattr(row, k, v)
    await session.commit()
    await session.refresh(row)
    return ImportResumoOut.model_validate(row, from_attributes=True)


# ── Frete (aba Frete do Celular, etapa 4) ──────────────────────────


def _frete_pct_for(prod: ImportProduct, cot_params: ImportCotacaoParams | None) -> Decimal:
    """Resolve o frete_pct efetivo do produto a partir do tipo +
    parâmetros globais. Default 0 quando não há params (categorias sem
    aba Cotação configurada). Garante Decimal pra não confundir
    Decimal*float em multiplicações abaixo."""
    if cot_params is None:
        return _ZERO
    t = (prod.frete_type or "regular").lower()
    val = {
        "regular": cot_params.frete_regular_pct,
        "swap": cot_params.frete_swap_pct,
        "acessorios": cot_params.frete_acessorios_pct,
    }.get(t)
    return Decimal(val) if val is not None else _ZERO


def _valor_unit_for(prod: ImportProduct) -> Decimal:
    """Valor unitário operacional pra aba Frete:
    `valor_brl_realizado` (preenchido pelo operador na aba Cotação);
    fallback pra `custo_bling` quando ainda não foi preenchido."""
    v = prod.valor_brl_realizado
    if v is not None and Decimal(v) > 0:
        return Decimal(v)
    return Decimal(prod.custo_bling or 0)


@router.get("/frete", response_model=ImportFreteList)
async def list_frete(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("importacao", "view"))],
    categoria: _CategoriaQ = "celular",
    transportadora: str | None = None,
    pago: bool | None = None,
) -> ImportFreteList:
    """Agregação da aba Frete. Combina (1) items dos lotes com seus
    produtos linkados + (2) ajustes manuais (linhas de ImportResumo
    com transportadora setada, na mesma categoria).

    - `valor_unit`: ImportProduct.valor_brl_realizado || custo_bling
    - `frete_pct`: do ImportProduct.frete_type + ImportCotacaoParams
    - `saldo`: total * frete_pct, mas SÓ quando lote tem fechamento
      (linhas pendentes mostram saldo=None na UI)
    Cards no topo:
    - `total_a_entregar`: SUM(total) de items em lotes sem fechamento
    - `saldo_a_pagar`: SUM(saldo) de items com fechamento E !pago
    """
    cot_params = (await session.execute(
        select(ImportCotacaoParams).where(ImportCotacaoParams.categoria == categoria)
    )).scalar_one_or_none()

    # Items dos lotes desta categoria + produto.
    items_q = (
        select(ImportLoteItem, ImportLote, ImportProduct)
        .join(ImportLote, ImportLote.id == ImportLoteItem.lote_id)
        .join(ImportProduct, ImportProduct.id == ImportLoteItem.product_id)
        .where(ImportLote.categoria == categoria)
        .order_by(ImportLote.abertura.desc(), ImportLote.nome, ImportProduct.sku)
    )
    if transportadora:
        items_q = items_q.where(ImportLote.transportadora == transportadora)
    if pago is not None:
        items_q = items_q.where(ImportLoteItem.pago == pago)

    item_rows = (await session.execute(items_q)).all()

    rows: list[ImportFreteRow] = []
    total_a_entregar = _ZERO
    saldo_a_pagar = _ZERO
    transportadora_set: set[str] = set()

    for item, lote, prod in item_rows:
        if lote.transportadora:
            transportadora_set.add(lote.transportadora)
        valor_unit = _valor_unit_for(prod)
        total = valor_unit * Decimal(item.quantidade or 0)
        frete_pct = _frete_pct_for(prod, cot_params)
        is_fechado = lote.fechamento is not None
        saldo = (total * frete_pct).quantize(Decimal("0.01")) if is_fechado else None
        rows.append(ImportFreteRow(
            kind="item",
            id=item.id,
            transportadora=lote.transportadora,
            lote_id=lote.id,
            lote_nome=lote.nome,
            abertura=lote.abertura,
            fechamento=lote.fechamento,
            modelo_bling=prod.modelo_bling,
            sku=prod.sku,
            quantidade=item.quantidade,
            valor_unit=valor_unit,
            total=total.quantize(Decimal("0.01")),
            frete_pct=frete_pct,
            saldo=saldo,
            pago=bool(item.pago),
            obs=None,
        ))
        if not is_fechado:
            total_a_entregar += total
        elif saldo is not None and not item.pago:
            saldo_a_pagar += saldo

    # Ajustes manuais — entries de ImportResumo com transportadora set.
    ajustes_q = (
        select(ImportResumo)
        .where(
            ImportResumo.categoria == categoria,
            ImportResumo.transportadora.isnot(None),
        )
        .order_by(ImportResumo.data.desc())
    )
    if transportadora:
        ajustes_q = ajustes_q.where(ImportResumo.transportadora == transportadora)

    for aj in (await session.execute(ajustes_q)).scalars().all():
        if aj.transportadora:
            transportadora_set.add(aj.transportadora)
        saldo_aj = Decimal(aj.saldo or 0)
        rows.append(ImportFreteRow(
            kind="ajuste",
            id=aj.id,
            transportadora=aj.transportadora,
            lote_id=None,
            lote_nome=aj.lote_nome,
            abertura=aj.data,
            fechamento=None,
            modelo_bling=None,
            sku=None,
            quantidade=None,
            valor_unit=None,
            total=None,
            frete_pct=None,
            saldo=saldo_aj,
            pago=False,
            obs=aj.obs,
        ))
        # Ajustes contam direto no "saldo a pagar" (sem conceito de
        # fechamento — operador já definiu o valor manualmente).
        saldo_a_pagar += saldo_aj

    return ImportFreteList(
        rows=rows,
        transportadoras=sorted(transportadora_set),
        total_a_entregar=total_a_entregar.quantize(Decimal("0.01")),
        saldo_a_pagar=saldo_a_pagar.quantize(Decimal("0.01")),
    )


@router.patch("/lote_item/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def patch_lote_item(
    item_id: UUID,
    body: ImportLoteItemPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("importacao", "edit"))],
) -> None:
    """Hoje só atualiza `pago` (toggle do checkbox na aba Frete)."""
    row = await session.get(ImportLoteItem, item_id)
    if row is None:
        raise HTTPException(404, detail={"code": "lote_item_not_found"})
    for k, v in body.model_dump(exclude_unset=True).items():
        if v is not None:
            setattr(row, k, v)
    await session.commit()


@router.post("/lote_ajuste", response_model=ImportResumoOut, status_code=status.HTTP_201_CREATED)
async def create_lote_ajuste(
    body: ImportFreteAjusteCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("importacao", "edit"))],
) -> ImportResumoOut:
    """Ajuste manual de frete — cria uma row em ImportResumo com
    transportadora setada. Aparece (1) na aba Frete (agregação) e (2)
    na aba Resumo (lançamento avulso, comportamento existente)."""
    row = ImportResumo(
        categoria=body.categoria,
        data=body.abertura,
        saldo=body.saldo,
        obs=body.obs,
        transportadora=body.transportadora,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return ImportResumoOut.model_validate(row, from_attributes=True)


# ── Cotação ────────────────────────────────────────────────────────
# Independente do resto do módulo — não puxa de import_products nem
# tem fórmulas. Tabela produto × fabricante, tudo digitado manualmente.


@router.get("/cotacao", response_model=CotacaoGridOut)
async def get_cotacao_grid(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("importacao", "view"))],
    categoria: _CategoriaQ = "mala",
) -> CotacaoGridOut:
    fabricantes = (await session.execute(
        select(CotacaoFabricante)
        .where(CotacaoFabricante.categoria == categoria)
        .order_by(CotacaoFabricante.ordem, CotacaoFabricante.created_at)
    )).scalars().all()
    produtos = (await session.execute(
        select(CotacaoProduto)
        .where(CotacaoProduto.categoria == categoria)
        .order_by(CotacaoProduto.ordem, CotacaoProduto.created_at)
    )).scalars().all()
    # Valores são scoped pela categoria via os fabricantes/produtos que
    # referenciam — basta restringir aos fabricantes desta categoria
    # (cada fabricante pertence a uma só categoria).
    fab_ids = [f.id for f in fabricantes]
    valores = (
        (await session.execute(
            select(CotacaoValor).where(CotacaoValor.fabricante_id.in_(fab_ids))
        )).scalars().all()
        if fab_ids else []
    )
    return CotacaoGridOut(
        fabricantes=[CotacaoFabricanteOut.model_validate(f, from_attributes=True) for f in fabricantes],
        produtos=[CotacaoProdutoOut.model_validate(p, from_attributes=True) for p in produtos],
        valores=[CotacaoValorOut.model_validate(v, from_attributes=True) for v in valores],
    )


@router.post(
    "/cotacao/fabricantes",
    response_model=CotacaoFabricanteOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_cotacao_fabricante(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("importacao", "edit"))],
    categoria: _CategoriaQ = "mala",
) -> CotacaoFabricanteOut:
    """Cria um fabricante vazio. Nome/obs são preenchidos depois via PATCH
    (autosave do frontend). `ordem` recebe MAX+1 (por categoria) para ir
    pro fim da lista."""
    next_ordem = (await session.execute(
        select(func.coalesce(func.max(CotacaoFabricante.ordem), -1) + 1)
        .where(CotacaoFabricante.categoria == categoria)
    )).scalar_one()
    row = CotacaoFabricante(nome="", ordem=int(next_ordem), categoria=categoria)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return CotacaoFabricanteOut.model_validate(row, from_attributes=True)


@router.patch("/cotacao/fabricantes/{row_id}", response_model=CotacaoFabricanteOut)
async def patch_cotacao_fabricante(
    row_id: UUID,
    body: CotacaoFabricantePatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("importacao", "edit"))],
) -> CotacaoFabricanteOut:
    row = await session.get(CotacaoFabricante, row_id)
    if row is None:
        raise HTTPException(404, detail={"code": "fabricante_not_found"})
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(row, k, v)
    await session.commit()
    await session.refresh(row)
    return CotacaoFabricanteOut.model_validate(row, from_attributes=True)


@router.delete("/cotacao/fabricantes/{row_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_cotacao_fabricante(
    row_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("importacao", "delete"))],
) -> None:
    row = await session.get(CotacaoFabricante, row_id)
    if row is None:
        raise HTTPException(404, detail={"code": "fabricante_not_found"})
    # FK ON DELETE CASCADE wipes all valores in this fabricante's column.
    await session.delete(row)
    await session.commit()


@router.post(
    "/cotacao/produtos",
    response_model=CotacaoProdutoOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_cotacao_produto(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("importacao", "edit"))],
    categoria: _CategoriaQ = "mala",
) -> CotacaoProdutoOut:
    next_ordem = (await session.execute(
        select(func.coalesce(func.max(CotacaoProduto.ordem), -1) + 1)
        .where(CotacaoProduto.categoria == categoria)
    )).scalar_one()
    row = CotacaoProduto(nome="", ordem=int(next_ordem), categoria=categoria)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return CotacaoProdutoOut.model_validate(row, from_attributes=True)


@router.patch("/cotacao/produtos/{row_id}", response_model=CotacaoProdutoOut)
async def patch_cotacao_produto(
    row_id: UUID,
    body: CotacaoProdutoPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("importacao", "edit"))],
) -> CotacaoProdutoOut:
    row = await session.get(CotacaoProduto, row_id)
    if row is None:
        raise HTTPException(404, detail={"code": "produto_not_found"})
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(row, k, v)
    await session.commit()
    await session.refresh(row)
    return CotacaoProdutoOut.model_validate(row, from_attributes=True)


@router.delete("/cotacao/produtos/{row_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_cotacao_produto(
    row_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("importacao", "delete"))],
) -> None:
    row = await session.get(CotacaoProduto, row_id)
    if row is None:
        raise HTTPException(404, detail={"code": "produto_not_found"})
    await session.delete(row)
    await session.commit()


@router.put("/cotacao/valores", response_model=CotacaoValorOut)
async def upsert_cotacao_valor(
    body: CotacaoValorUpsert,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("importacao", "edit"))],
) -> CotacaoValorOut:
    """Upsert da célula (fabricante × produto). Idempotente — frontend
    chama a cada edição inline. Quando os 3 campos (capacidade,
    valor_real, valor_usd) ficam todos nulos/vazios, a célula é
    deletada para manter a tabela limpa."""
    existing = (await session.execute(
        select(CotacaoValor).where(
            and_(
                CotacaoValor.fabricante_id == body.fabricante_id,
                CotacaoValor.produto_id == body.produto_id,
            )
        )
    )).scalar_one_or_none()

    is_empty = (
        (body.capacidade is None or body.capacidade == "")
        and body.valor_real is None
        and body.valor_usd is None
    )

    if existing is None:
        if is_empty:
            # Nothing to store; return a synthetic empty row so the
            # frontend has a stable response shape.
            return CotacaoValorOut(
                id=UUID(int=0),
                fabricante_id=body.fabricante_id,
                produto_id=body.produto_id,
            )
        row = CotacaoValor(
            fabricante_id=body.fabricante_id,
            produto_id=body.produto_id,
            capacidade=body.capacidade or None,
            valor_real=body.valor_real,
            valor_usd=body.valor_usd,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return CotacaoValorOut.model_validate(row, from_attributes=True)

    if is_empty:
        await session.delete(existing)
        await session.commit()
        return CotacaoValorOut(
            id=UUID(int=0),
            fabricante_id=body.fabricante_id,
            produto_id=body.produto_id,
        )

    existing.capacidade = body.capacidade or None
    existing.valor_real = body.valor_real
    existing.valor_usd = body.valor_usd
    await session.commit()
    await session.refresh(existing)
    return CotacaoValorOut.model_validate(existing, from_attributes=True)


# ── Kit ────────────────────────────────────────────────────────────
# Aba "Kit": matriz produto × variação. Variations e bases são seeded
# fixos (migration 0099) — operador apenas toggle marks. Fase 1 só UI;
# integração com Bling/Tabela de Preços fica pras fases 2/3.


@router.get("/kit", response_model=ImportKitGridOut)
async def get_kit_grid(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("importacao", "view"))],
    categoria: _CategoriaQ = "mala",
) -> ImportKitGridOut:
    # Eletro não tem aba Kit (produtos não viram composto).
    if categoria == "eletro":
        raise HTTPException(404, detail={"code": "kit_not_available_for_categoria"})
    variations = (await session.execute(
        select(ImportKitVariation)
        .where(ImportKitVariation.categoria == categoria)
        .order_by(ImportKitVariation.ordem)
    )).scalars().all()
    bases = (await session.execute(
        select(ImportKitBase)
        .where(ImportKitBase.categoria == categoria)
        .order_by(ImportKitBase.ordem)
    )).scalars().all()
    marks = (await session.execute(
        select(ImportKitMark).where(ImportKitMark.categoria == categoria)
    )).scalars().all()
    return ImportKitGridOut(
        variations=[ImportKitVariationOut.model_validate(v, from_attributes=True) for v in variations],
        bases=[ImportKitBaseOut.model_validate(b, from_attributes=True) for b in bases],
        marks=[ImportKitMarkOut.model_validate(m, from_attributes=True) for m in marks],
    )


@router.put("/kit/mark", status_code=status.HTTP_204_NO_CONTENT)
async def toggle_kit_mark(
    body: ImportKitMarkToggle,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("importacao", "edit"))],
) -> None:
    """Toggle idempotente. Quando `marked=True` e a mark não existe,
    cria com `bling_sync_status='pending'` e enfileira job ARQ
    `create_bling_kit_for_mark_job` que cria o composto no Bling.

    Quando `marked=False`, deleta a mark local. NÃO apaga o produto
    no Bling — operação destrutiva fica pra ser feita manualmente.
    A UI mostra warning antes de desmarcar rows com bling_product_id.
    """
    existing = (await session.execute(
        select(ImportKitMark).where(
            ImportKitMark.base_id == body.base_id,
            ImportKitMark.variation_id == body.variation_id,
        )
    )).scalar_one_or_none()
    if body.marked and existing is None:
        # Mark herda a categoria da sua base (kit é mala/celular).
        base = await session.get(ImportKitBase, body.base_id)
        mark = ImportKitMark(
            base_id=body.base_id,
            variation_id=body.variation_id,
            categoria=base.categoria if base else "mala",
            bling_sync_status="pending",
        )
        session.add(mark)
        await session.commit()
        await session.refresh(mark)
        # Enfileirar criação do composto no Bling (fire-and-forget).
        try:
            pool = await get_arq_pool()
            await pool.enqueue_job("create_bling_kit_for_mark_job", str(mark.id))
        except Exception as e:  # noqa: BLE001
            # Não derruba a UI se o ARQ estiver indisponível — operador
            # pode usar resync depois.
            logger.warning("kit_enqueue_failed", mark_id=str(mark.id), err=str(e)[:200])
    elif not body.marked and existing is not None:
        await session.delete(existing)
        await session.commit()


@router.post(
    "/kit/mark/{mark_id}/resync",
    response_model=ImportKitMarkOut,
)
async def resync_kit_mark(
    mark_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("importacao", "edit"))],
) -> ImportKitMarkOut:
    """Re-enfileira o job de criação no Bling pra uma mark — usado
    quando a primeira tentativa falhou (status='error'). Também
    reseta o estado de pricing_sync caso operador queira refazer o
    ciclo todo. Limpa error/attempt fields."""
    mark = (await session.execute(
        select(ImportKitMark).where(ImportKitMark.id == mark_id)
    )).scalar_one_or_none()
    if mark is None:
        raise HTTPException(404, detail={"code": "mark_not_found"})
    if mark.bling_product_id is not None:
        # Bling já criado — nada a fazer aqui. Pra pricing, use o
        # endpoint /resync-pricing.
        return ImportKitMarkOut.model_validate(mark, from_attributes=True)
    mark.bling_sync_status = "pending"
    mark.bling_sync_error = None
    # Reset pricing também — pricing depende do bling, então qualquer
    # estado de pricing anterior fica inconsistente.
    mark.pricing_sync_status = None
    mark.pricing_sync_error = None
    await session.commit()
    await session.refresh(mark)
    try:
        pool = await get_arq_pool()
        await pool.enqueue_job("create_bling_kit_for_mark_job", str(mark.id))
    except Exception as e:  # noqa: BLE001
        logger.warning("kit_resync_enqueue_failed", mark_id=str(mark.id), err=str(e)[:200])
    return ImportKitMarkOut.model_validate(mark, from_attributes=True)


@router.post(
    "/kit/mark/{mark_id}/resync-pricing",
    response_model=ImportKitMarkOut,
)
async def resync_kit_pricing(
    mark_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("importacao", "edit"))],
) -> ImportKitMarkOut:
    """Re-tenta SÓ a parte de pricing_product (assume Bling já em
    'sent'). Útil quando o Bling criou ok mas o pricing falhou e o
    operador quer re-tentar sem refazer o Bling create."""
    # Import inline pra evitar circular (router → services → router).
    from app.services.bling_kit_create import retry_pricing_sync_for_mark

    mark = (await session.execute(
        select(ImportKitMark).where(ImportKitMark.id == mark_id)
    )).scalar_one_or_none()
    if mark is None:
        raise HTTPException(404, detail={"code": "mark_not_found"})
    if mark.bling_product_id is None:
        raise HTTPException(
            409,
            detail={"code": "bling_not_synced_yet"},
        )
    # Reset pra pending e executa inline (sync rápido — só DB local).
    mark.pricing_sync_status = "pending"
    mark.pricing_sync_error = None
    await session.commit()

    result = await retry_pricing_sync_for_mark(mark_id)
    # Re-fetch após o helper commitar.
    refreshed = (await session.execute(
        select(ImportKitMark).where(ImportKitMark.id == mark_id)
    )).scalar_one_or_none()
    if refreshed is None:
        raise HTTPException(404, detail={"code": "mark_not_found"})
    if not result.get("ok"):
        # Caller pode inspecionar mark.pricing_sync_error.
        logger.info(
            "kit_pricing_resync_returned_error",
            mark_id=str(mark_id), error=result.get("error"),
        )
    return ImportKitMarkOut.model_validate(refreshed, from_attributes=True)
