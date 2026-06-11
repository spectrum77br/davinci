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

import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, func, or_, select, text
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
    ImportKitVariationCreate,
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
from app.services.importacao_naming import generate_product_name, parse_kit_variation
from app.services.pricing.audit import build_match_indexes, match_one_sku_to_keys
from app.worker_pool import get_arq_ui_pool

logger = structlog.get_logger()
router = APIRouter(prefix="/api/importacao", tags=["importacao"])

_ZERO = Decimal("0")

# Selector top-level: cada categoria tem seus próprios dados. Filtro
# aplicado em todos os GETs; create grava a categoria recebida.
_CATEGORIAS = ("mala", "eletro", "celular")
_CategoriaQ = Annotated[str, Query(pattern="^(mala|eletro|celular)$")]


# ── Config por categoria ─────────────────────────────────────────────


# Defaults pra auto-criar a row da categoria no 1º acesso. Os mesmos
# valores que a seed da migration 0132 grava pra 'celular'. Mala já
# tem a row carimbada da era singleton (qualquer valor que o operador
# tinha antes do split).
_CONFIG_DEFAULTS = {"tempo_reposicao": 150, "tempo_estoque": 60}


async def _get_or_create_config(session: AsyncSession, categoria: str) -> ImportConfig:
    """Busca a row de `import_config` pra categoria; cria com defaults
    se não existir. Espelha get_cotacao_params (também por categoria)."""
    row = (await session.execute(
        select(ImportConfig).where(ImportConfig.categoria == categoria)
    )).scalar_one_or_none()
    if row is None:
        row = ImportConfig(categoria=categoria, **_CONFIG_DEFAULTS)
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return row


@router.get("/config", response_model=ImportConfigOut)
async def get_config(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("importacao", "view"))],
    categoria: _CategoriaQ = "celular",
) -> ImportConfigOut:
    row = await _get_or_create_config(session, categoria)
    return ImportConfigOut.model_validate(row, from_attributes=True)


@router.patch("/config", response_model=ImportConfigOut)
async def patch_config(
    body: ImportConfigPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("importacao", "edit"))],
    categoria: _CategoriaQ = "celular",
) -> ImportConfigOut:
    row = await _get_or_create_config(session, categoria)
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
        custo_realizado=None,
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
      * reposicao_estoque = (dias_necessarios − duração_estoque) * memoria
      * saldo_reposicao = reposicao_estoque − pedidos_em_aberto

    Usar `memoria` (e não `consumo_diario` direto) garante que produtos
    em ruptura — onde consumo_diario virou ~0 porque o estoque zerou —
    ainda gerem reposição proporcional à demanda histórica.
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
    # Spec do operador: usar `memoria` (que já gate por estoque) em vez
    # de consumo_diario direto. Em ruptura (estoque=0), consumo_diario
    # cai pra ~0 e a fórmula antiga zerava a reposição mesmo havendo
    # demanda histórica via maior_media_30d.
    reposicao_dec = saldo_dias * memoria
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
    # Ordena por modelo_bling alfabético (case-insensitive) — mesma
    # convenção que GET /kit usa pra bases (commit 298b661). Antes era
    # por sku, o que em celular punha Fossibot (sku dg*) antes de Apple
    # (sku i*). Mala já bate visualmente porque sku/modelo são alinhados
    # lá. NULLs no fim. `sku` é tiebreaker quando 2 SKUs têm o mesmo
    # modelo (i228.sp vs i228.sa pra "Macbook Air M5 Cinza").
    products = (
        await session.execute(
            select(ImportProduct)
            .where(ImportProduct.categoria == categoria)
            .order_by(
                ImportProduct.modelo_bling.is_(None),
                func.lower(ImportProduct.modelo_bling),
                ImportProduct.sku,
            )
        )
    ).scalars().all()

    # Config (tempo_reposicao + tempo_estoque) agora é por categoria
    # (migration 0132). Cada cell de reposição/saldo usa esses valores
    # via _compute_product_fields — antes vazava o valor da mala pro
    # celular porque era singleton.
    cfg = await _get_or_create_config(session, categoria)

    # Open-lote items: aggregate for pedidos_em_aberto + per-(lote,product) map.
    open_items = (
        await session.execute(
            select(ImportLoteItem, ImportLote)
            .join(ImportLote, ImportLote.id == ImportLoteItem.lote_id)
        )
    ).all()
    qty_by_product_open: dict[UUID, int] = {}
    qty_by_pair: dict[UUID, dict[str, int]] = {}
    # Paralelo a qty_by_pair, valor_usd por (produto, lote) — só celular
    # usa, mas montamos pra todos sem custo extra (1 loop só).
    valor_usd_by_pair: dict[UUID, dict[str, Decimal | None]] = {}
    # Custo BRL manual por (produto, lote) — usado em lotes sem
    # taxa/frete (i48 e similares). Migration 0128.
    custo_manual_by_pair: dict[UUID, dict[str, Decimal | None]] = {}
    # Override do SKU destino da entrada de estoque no Bling, por
    # (produto, lote). Frontend usa pro dropdown da aba Celular.
    # `item_id_by_pair` pareado pra o PATCH /lote_item/{id}. Migration 0138.
    target_sku_by_pair: dict[UUID, dict[str, str | None]] = {}
    item_id_by_pair: dict[UUID, dict[str, str]] = {}
    # Acumuladores pro custo_realizado computed (média ponderada por qty
    # do custoBRL de cada lote onde o produto aparece). Σ(qty × custoBRL)
    # no numerador, Σ(qty) no denominador. Só items com valor_usd não-null
    # e qty > 0 entram — sem ambos não há "custo" pra ponderar.
    custo_realizado_num: dict[UUID, Decimal] = {}
    custo_realizado_den: dict[UUID, int] = {}
    for item, lote in open_items:
        qty_by_pair.setdefault(item.product_id, {})[str(item.lote_id)] = item.quantidade
        valor_usd_by_pair.setdefault(item.product_id, {})[str(item.lote_id)] = item.valor_usd
        custo_manual_by_pair.setdefault(item.product_id, {})[str(item.lote_id)] = item.custo_manual
        target_sku_by_pair.setdefault(item.product_id, {})[str(item.lote_id)] = item.bling_stock_target_sku
        item_id_by_pair.setdefault(item.product_id, {})[str(item.lote_id)] = str(item.id)
        if lote.fechamento is None:
            qty_by_product_open[item.product_id] = (
                qty_by_product_open.get(item.product_id, 0) + (item.quantidade or 0)
            )
        qty = int(item.quantidade or 0)
        if qty <= 0 or item.valor_usd is None:
            continue
        # custoBRL = valor_usd × taxa × (1 + frete_pct) + adicional, com
        # params do PRÓPRIO LOTE. NULLs em qualquer param → linha ignorada
        # (não dá pra calcular). Mantém celular consistente com o body.
        if lote.taxa is None or lote.frete_pct is None or lote.adicional is None:
            continue
        custo_brl = (
            Decimal(item.valor_usd) * Decimal(lote.taxa)
            * (Decimal("1") + Decimal(lote.frete_pct))
            + Decimal(lote.adicional)
        )
        custo_realizado_num[item.product_id] = (
            custo_realizado_num.get(item.product_id, Decimal("0"))
            + Decimal(qty) * custo_brl
        )
        custo_realizado_den[item.product_id] = (
            custo_realizado_den.get(item.product_id, 0) + qty
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
            custo_realizado=(
                (custo_realizado_num[p.id] / Decimal(custo_realizado_den[p.id]))
                .quantize(Decimal("0.01"))
                if custo_realizado_den.get(p.id, 0) > 0
                else None
            ),
            lote_quantidades=qty_by_pair.get(p.id, {}),
            lote_valores_usd=valor_usd_by_pair.get(p.id, {}),
            lote_custos_manuais=custo_manual_by_pair.get(p.id, {}),
            lote_target_skus=target_sku_by_pair.get(p.id, {}),
            lote_item_ids=item_id_by_pair.get(p.id, {}),
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
        # custo_realizado NÃO computado aqui (resposta de create/patch
        # de UM produto, não tem o cross-join com lote items). Frontend
        # busca via /products?categoria=... depois pra ter o valor real.
        custo_realizado=None,
        lote_quantidades={},
        lote_valores_usd={},
        lote_custos_manuais={},
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
        # custo_realizado NÃO computado aqui (resposta de create/patch
        # de UM produto, não tem o cross-join com lote items). Frontend
        # busca via /products?categoria=... depois pra ter o valor real.
        custo_realizado=None,
        lote_quantidades={},
        lote_valores_usd={},
        lote_custos_manuais={},
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
        pool = await get_arq_ui_pool()
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
        # custo_realizado NÃO computado aqui (resposta de create/patch
        # de UM produto, não tem o cross-join com lote items). Frontend
        # busca via /products?categoria=... depois pra ter o valor real.
        custo_realizado=None,
        lote_quantidades={},
        lote_valores_usd={},
        lote_custos_manuais={},
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
      * Celular com taxa+frete_pct → SUM(custoBRL(valor_usd) × qty)
        usando params do próprio lote (fallback ImportCotacaoParams pra
        `adicional` quando não setado).
      * Celular sem taxa OU sem frete_pct (ex: i48, acessórios em
        massa) → SUM(item.custo_manual × qty). A fórmula USD×taxa não
        se aplica; operador digita o BRL direto por linha (migration
        0128).

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
        has_formula = lote.taxa is not None and lote.frete_pct is not None
        if has_formula:
            # Pra celular com taxa+frete: SUM(custoBRL × qty) — params
            # do próprio LOTE (taxa/frete_pct/adicional) e valor_usd
            # POR LOTE (item.valor_usd; fallback pra produto.valor_usd
            # quando item ainda não tem).
            cot_fallback = (await session.execute(
                select(ImportCotacaoParams).where(ImportCotacaoParams.categoria == "celular")
            )).scalar_one_or_none()
            cambio = Decimal(lote.taxa)
            frete = Decimal(lote.frete_pct)
            adic = Decimal(lote.adicional) if lote.adicional is not None else (
                Decimal(cot_fallback.adicional) if cot_fallback else Decimal("0")
            )
            realizado_row = await session.execute(
                select(
                    ImportLoteItem.quantidade,
                    ImportLoteItem.valor_usd,
                    ImportProduct.valor_usd,
                )
                .select_from(ImportLoteItem)
                .join(ImportProduct, ImportProduct.id == ImportLoteItem.product_id)
                .where(ImportLoteItem.lote_id == lote.id)
            )
            realizado = Decimal("0")
            for qty, item_usd, prod_usd in realizado_row.all():
                usd = item_usd if item_usd is not None else prod_usd
                if usd is None or qty is None or Decimal(usd) <= 0 or int(qty) <= 0:
                    continue
                custo_unit = Decimal(usd) * cambio * (Decimal("1") + frete) + adic
                realizado += custo_unit * Decimal(int(qty))
        else:
            # Lote sem taxa/frete (i48 e similares): custo BRL é digitado
            # manualmente por linha em item.custo_manual.
            manual_rows = await session.execute(
                select(ImportLoteItem.quantidade, ImportLoteItem.custo_manual)
                .where(ImportLoteItem.lote_id == lote.id)
            )
            realizado = Decimal("0")
            for qty, cm in manual_rows.all():
                if cm is None or qty is None or int(qty) <= 0:
                    continue
                realizado += Decimal(cm) * Decimal(int(qty))
        realizado = realizado.quantize(Decimal("0.01"))
    else:
        realizado = Decimal(lote.realizado or 0)

    saldo = previsto - realizado
    prazo = (lote.fechamento - lote.abertura).days if lote.fechamento else None

    # Migration 0138: agregados pro badge "Bling stock" no header do
    # lote. Só vale pra Celular (única categoria que dispara entrada de
    # estoque ao fechar); pras outras fica tudo zerado.
    stock_total = stock_sent = stock_skipped = stock_errors = 0
    if lote.categoria == "celular":
        st_rows = (await session.execute(
            select(
                ImportLoteItem.bling_stock_status,
                func.count().label("c"),
            )
            .where(ImportLoteItem.lote_id == lote.id)
            .group_by(ImportLoteItem.bling_stock_status)
        )).all()
        for status_val, cnt in st_rows:
            stock_total += int(cnt)
            if status_val == "sent":
                stock_sent += int(cnt)
            elif status_val == "skipped":
                stock_skipped += int(cnt)
            elif status_val == "error":
                stock_errors += int(cnt)

    return ImportLoteOut(
        id=lote.id, categoria=lote.categoria, nome=lote.nome, abertura=lote.abertura,
        fechamento=lote.fechamento, realizado=realizado,
        transportadora=lote.transportadora, obs=lote.obs,
        previsto_manual=lote.previsto_manual,
        taxa=lote.taxa, frete_pct=lote.frete_pct, adicional=lote.adicional,
        previsto=previsto, saldo=saldo, prazo=prazo,
        is_aberto=lote.fechamento is None,
        bling_stock_total=stock_total,
        bling_stock_sent=stock_sent,
        bling_stock_skipped=stock_skipped,
        bling_stock_errors=stock_errors,
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
    # Pra celular, prefill taxa/frete_pct/adicional da ImportCotacaoParams
    # quando o body não vier com valores explícitos. Mala ignora (campos
    # ficam NULL). Operador pode editar por lote depois via PATCH.
    taxa = body.taxa
    frete_pct = body.frete_pct
    adicional = body.adicional
    if body.categoria == "celular" and (taxa is None or frete_pct is None or adicional is None):
        cot = (await session.execute(
            select(ImportCotacaoParams).where(ImportCotacaoParams.categoria == "celular")
        )).scalar_one_or_none()
        if cot is not None:
            if taxa is None:
                taxa = cot.taxa_cambio
            if frete_pct is None:
                frete_pct = cot.frete_regular_pct
            if adicional is None:
                adicional = cot.adicional
    row = ImportLote(
        nome=body.nome, abertura=body.abertura, categoria=body.categoria,
        transportadora=body.transportadora, obs=body.obs,
        taxa=taxa, frete_pct=frete_pct, adicional=adicional,
    )
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
        # Celular: enfileira entrada de estoque no Bling pra cada item.
        # Operação 'E' (soma) — operador pediu só a quantidade, sem custo.
        # Job é idempotente (pula items com bling_stock_pushed_at), então
        # re-fechar um lote (ex.: reabrir/fechar de novo) não duplica.
        if row.categoria == "celular":
            pool = await get_arq_ui_pool()
            await pool.enqueue_job("push_lote_stock_to_bling_job", str(row.id))
            logger.info(
                "importacao_lote_bling_stock_enqueued",
                lote_id=str(row.id), nome=row.nome,
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
    """Upsert (lote, product) — quantidade + valor_usd opcional. Quantidade
    0 deleta a row inteira. Body com `valor_usd=None` mantém o valor
    existente (model_dump exclude_unset não passa por aqui — `valor_usd`
    sempre é setado quando vem no body, então `None` significa "limpa".
    Pra manter, NÃO passe o campo)."""
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

    # Quantidade 0 + sem valor_usd → delete. Se vier valor_usd não-null,
    # operador pode estar setando "só o preço" antes de definir a qty —
    # nesse caso cria/atualiza a row com qty=0 e o valor.
    if body.quantidade == 0 and body.valor_usd is None:
        if existing is not None:
            await session.delete(existing)
            await session.commit()
        return {"ok": True, "quantidade": 0}

    fields = body.model_dump(exclude_unset=True)
    if existing is None:
        new_item = ImportLoteItem(
            lote_id=lote_id, product_id=body.product_id,
            quantidade=body.quantidade,
            valor_usd=body.valor_usd,
        )
        session.add(new_item)
        await session.commit()
        item_id = new_item.id
    else:
        existing.quantidade = body.quantidade
        if "valor_usd" in fields:
            existing.valor_usd = body.valor_usd
        await session.commit()
        item_id = existing.id
    return {
        "ok": True,
        "quantidade": body.quantidade,
        "valor_usd": float(body.valor_usd) if body.valor_usd is not None else None,
        # item_id retornado pra o frontend conseguir disparar um PATCH
        # subsequente em campos específicos (ex: custo_manual em lotes
        # sem taxa/frete). Migration 0128.
        "item_id": str(item_id),
    }


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
    """Atualiza campos editáveis de uma linha do Resumo. Cobre tanto
    "anotar obs" (uso original) quanto "editar ajuste manual de frete"
    (transportadora/data/saldo/lote_nome).

    Esvaziar `transportadora` removeria a row da aba Frete (filtro é
    `transportadora IS NOT NULL` em list_frete) — bloqueado aqui pra
    evitar perda silenciosa.
    """
    row = await session.get(ImportResumo, row_id)
    if row is None:
        raise HTTPException(404, detail={"code": "resumo_not_found"})
    fields = body.model_dump(exclude_unset=True)
    if "transportadora" in fields:
        v = fields["transportadora"]
        if v is None or (isinstance(v, str) and not v.strip()):
            raise HTTPException(
                422, detail={"code": "transportadora_required"},
            )
    for k, v in fields.items():
        if v is not None:
            setattr(row, k, v)
    await session.commit()
    await session.refresh(row)
    return ImportResumoOut.model_validate(row, from_attributes=True)


# ── Frete (aba Frete do Celular, etapa 4) ──────────────────────────


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

    Fórmula (em DÓLAR — Excel do operador):
    - `valor_unit`: item.valor_usd (preço unitário no lote)
    - `total`: valor_usd × quantidade
    - `frete_pct`: lote.frete_pct (frete do LOTE — não do produto)
    - `saldo`: total × frete_pct sempre que `total` existir — projeção
      pré-fechamento e débito pós-fechamento. O frontend distingue
      visualmente (vermelho só pra dívida real: lote fechado + !pago).

    Cards no topo (todos em US$):
    - `total_a_entregar`: SUM(total) de items em lotes sem fechamento
    - `saldo_a_pagar`: SUM(saldo) de items com fechamento E !pago.
      O gate "só conta no a pagar quando fechado" vive APENAS no
      acumulador abaixo (if/elif), NÃO na computação do saldo da linha.
    """
    # Items dos lotes desta categoria + produto. Filtra:
    # - `lote.frete_pct IS NOT NULL`: frete % é parte da fórmula, sem ele
    #   não há como calcular saldo.
    # - `item.custo_manual IS NULL`: lotes de custo digitado na mão (I48,
    #   acessórios em massa) ficam fora — não passam por transportadora
    #   regular. Diferente do filtro antigo (taxa IS NOT NULL), que
    #   sumia indevidamente lotes válidos sem câmbio definido (ex: AG257),
    #   porque a aba Frete é toda em USD — câmbio não entra.
    items_q = (
        select(ImportLoteItem, ImportLote, ImportProduct)
        .join(ImportLote, ImportLote.id == ImportLoteItem.lote_id)
        .join(ImportProduct, ImportProduct.id == ImportLoteItem.product_id)
        .where(ImportLote.categoria == categoria)
        .where(ImportLote.frete_pct.isnot(None))
        .where(ImportLoteItem.custo_manual.is_(None))
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
        # Tudo em USD. `valor_usd` é o preço unitário do produto NAQUELE
        # lote (item.valor_usd). Quando None, a linha aparece sem
        # valor/total/saldo — operador ainda não preencheu na aba
        # Importação.
        valor_unit = Decimal(item.valor_usd) if item.valor_usd is not None else None
        qty = Decimal(item.quantidade or 0)
        total = valor_unit * qty if valor_unit is not None else None
        frete_pct = Decimal(lote.frete_pct)
        is_fechado = lote.fechamento is not None
        saldo = (
            (total * frete_pct).quantize(Decimal("0.01"))
            if total is not None
            else None
        )
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
            total=total.quantize(Decimal("0.01")) if total is not None else None,
            frete_pct=frete_pct,
            saldo=saldo,
            pago=bool(item.pago),
            obs=None,
        ))
        if not is_fechado and total is not None:
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
    """Atualiza `pago` (toggle da aba Frete) ou `custo_manual` (input
    do custo BRL na aba Importação quando o lote não tem taxa/frete).
    `custo_manual=None` no body limpa o campo (volta a usar fórmula,
    se aplicável)."""
    row = await session.get(ImportLoteItem, item_id)
    if row is None:
        raise HTTPException(404, detail={"code": "lote_item_not_found"})
    fields = body.model_dump(exclude_unset=True)
    # `pago` é boolean — limpar não faz sentido, manter o skip-None
    # original.
    if "pago" in fields and fields["pago"] is not None:
        row.pago = fields["pago"]
    # `custo_manual` aceita None explícito pra limpar.
    if "custo_manual" in fields:
        row.custo_manual = fields["custo_manual"]
    # `bling_stock_target_sku` aceita None explícito pra limpar (volta
    # a usar o SKU do ImportProduct na hora de mandar pro Bling).
    target_sku_changed = False
    if "bling_stock_target_sku" in fields:
        v = fields["bling_stock_target_sku"]
        new_target = v.strip() if isinstance(v, str) and v.strip() else None
        target_sku_changed = row.bling_stock_target_sku != new_target
        row.bling_stock_target_sku = new_target
    await session.commit()

    # Se o operador trocou o SKU destino e o item já tinha sido tentado
    # (skipped/error sem pushed_at), re-enfileira o job pro lote. O
    # service é idempotente — só items com bling_stock_pushed_at IS NULL
    # são processados, então não duplica os já enviados. Cobre o caso
    # "lote fechou, 1 item pulou por SKU errado, operador escolhe o
    # certo, retry automático" sem precisar reabrir o lote.
    if target_sku_changed and row.bling_stock_pushed_at is None and (
        row.bling_stock_status in ("skipped", "error")
    ):
        lote_id = (await session.execute(
            select(ImportLote.id, ImportLote.fechamento)
            .where(ImportLote.id == row.lote_id)
        )).one_or_none()
        if lote_id is not None and lote_id.fechamento is not None:
            pool = await get_arq_ui_pool()
            await pool.enqueue_job("push_lote_stock_to_bling_job", str(row.lote_id))
            logger.info(
                "importacao_lote_bling_stock_retry_enqueued",
                lote_id=str(row.lote_id), item_id=str(row.id),
                target_sku=row.bling_stock_target_sku,
            )


# Suffixes de tag usados no SKU (.ci/.pi/.ra/.sa/.sp/.us/.cd). Espelha
# SUFFIX_TAGS em app.services.sku_tags. Usado pra calcular o "prefixo
# base" do SKU (ex.: i203.sa → i203) no endpoint de variantes.
_SKU_SUFFIX_RE = re.compile(r"\.(ci|pi|ra|sa|sp|us|cd)$", re.IGNORECASE)


@router.get("/lote_item/{item_id}/sku-variants")
async def list_lote_item_sku_variants(
    item_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("importacao", "view"))],
) -> dict:
    """Lista os SKUs disponíveis no Bling com o mesmo prefixo base do
    SKU do item (ex.: i203.sa, i203.sp pra um item com SKU i203.sa).
    Usado pelo dropdown 'destino' na aba Importação Celular.

    Base = SKU sem o sufixo de tag (.ci/.pi/.ra/.sa/.sp/.us/.cd).
    Inclui o próprio base sem sufixo se existir como produto ativo.
    Só retorna produtos com `bling_product_id` setado (caso contrário
    o service não conseguiria enviar estoque)."""
    row = (await session.execute(
        select(ImportLoteItem.bling_stock_target_sku, ImportProduct.sku)
        .join(ImportProduct, ImportProduct.id == ImportLoteItem.product_id)
        .where(ImportLoteItem.id == item_id)
    )).one_or_none()
    if row is None:
        raise HTTPException(404, detail={"code": "lote_item_not_found"})
    target_sku, product_sku = row
    base_sku = _SKU_SUFFIX_RE.sub("", (product_sku or "").strip())
    if not base_sku:
        return {"base": "", "current": target_sku or product_sku, "variants": []}

    base_lower = base_sku.lower()
    variant_re = rf"^{re.escape(base_lower)}\.(ci|pi|ra|sa|sp|us|cd)$"
    rows = (await session.execute(
        select(Product.sku, Product.name)
        .where(
            or_(
                func.lower(func.btrim(Product.sku)) == base_lower,
                func.lower(func.btrim(Product.sku)).op("~")(variant_re),
            ),
            Product.bling_product_id.is_not(None),
            or_(Product.situacao == "A", Product.situacao.is_(None)),
        )
    )).all()
    seen: dict[str, str | None] = {}
    for sku, name in rows:
        k = (sku or "").strip()
        if not k:
            continue
        if k.lower() not in seen:
            seen[k.lower()] = name
    variants = [
        {"sku": k, "name": v}
        for k, v in sorted(seen.items(), key=lambda kv: kv[0])
    ]
    return {
        "base": base_sku,
        "current": target_sku or product_sku,
        "variants": variants,
    }


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
        lote_nome=body.lote_nome,
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


# Regex pra code de variação celular: `aXXX` ou combinação `aX+aY+...`.
# Ex válidos: a001, a003, a003+a004, a007+a012. Inválidos: 8+18 (mala),
# bp001, vazio.
_CELULAR_CODE_RE = re.compile(r"^a\d+(\+a\d+)*$")


def _validate_kit_variation_code(categoria: str, code: str) -> None:
    """Valida o `code` de uma variation de kit conforme a categoria.

    Mala: requer ao menos 1 tamanho numérico (acessórios opcionais).
      Aceita "8", "8+18", "12+20+24+a075", "12,14,16". Reusa o parser
      que o resto do pipeline já usa (parse_kit_variation).

    Celular: padrão estrito `a\\d+(\\+a\\d+)*`. Operador só usa
      acessórios standalone como variation; tamanhos numéricos não
      fazem sentido (celular não tem variação por tamanho).

    Levanta HTTPException 422 com `code: invalid_variation_code` se
    rejeitar. Mensagem específica por categoria no `message`."""
    code = (code or "").strip()
    if not code:
        raise HTTPException(422, detail={
            "code": "invalid_variation_code", "message": "code is required",
        })
    if categoria == "celular":
        if not _CELULAR_CODE_RE.match(code):
            raise HTTPException(422, detail={
                "code": "invalid_variation_code",
                "message": "celular: use formato 'aXXX' ou 'aXXX+aYYY'",
            })
        return
    # Mala: parse e exige pelo menos 1 tamanho numérico (acessórios soltos
    # podem ser na celular; mala kit precisa do tamanho como âncora).
    sizes, _accessories = parse_kit_variation(code)
    if not sizes:
        raise HTTPException(422, detail={
            "code": "invalid_variation_code",
            "message": "mala: requer pelo menos 1 tamanho numérico (ex: 8, 12+20)",
        })


@router.post(
    "/kit/variations",
    response_model=ImportKitVariationOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_kit_variation(
    body: ImportKitVariationCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("importacao", "edit"))],
) -> ImportKitVariationOut:
    """Cria uma nova variação de kit. Categoria + code validados aqui;
    `ordem` calculada como MAX(ordem) + 1 dentro da categoria. Conflito
    em (categoria, code) → 409."""
    cat = (body.categoria or "").lower().strip()
    if cat not in ("mala", "celular"):
        raise HTTPException(422, detail={
            "code": "invalid_categoria",
            "message": "categoria deve ser 'mala' ou 'celular' (eletro não tem kit)",
        })
    _validate_kit_variation_code(cat, body.code)
    label = (body.label or "").strip()
    if not label:
        raise HTTPException(422, detail={"code": "label_required"})
    if len(label) > 60:
        raise HTTPException(422, detail={"code": "label_too_long", "max": 60})

    # Conflito por (categoria, code). Celular tem UNIQUE parcial no DB
    # (migration 0117) — IntegrityError viraria 500; antecipamos com
    # SELECT pra ter 409 limpo. Mala tem duplicata legítima histórica
    # (pos 19/20) então NÃO há UNIQUE de DB — só este check evita
    # criar mais duplicatas via UI.
    code = body.code.strip()
    existing = (await session.execute(
        select(ImportKitVariation).where(
            ImportKitVariation.categoria == cat,
            ImportKitVariation.code == code,
        ).limit(1)
    )).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(409, detail={
            "code": "variation_code_exists",
            "message": f"Já existe variação {cat} com esse código",
            "existing_id": str(existing.id),
        })

    # Próxima ordem na categoria. `coalesce` garante MAX+1 mesmo quando
    # a categoria não tem nenhuma variation ainda (start em 1).
    next_ordem = (await session.execute(
        select(func.coalesce(func.max(ImportKitVariation.ordem), 0) + 1)
        .where(ImportKitVariation.categoria == cat)
    )).scalar() or 1

    row = ImportKitVariation(
        categoria=cat, code=code, label=label,
        ordem=int(next_ordem), highlight=False,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return ImportKitVariationOut.model_validate(row, from_attributes=True)


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
    # Bases ordenadas alfabeticamente por modelo_bling (case-insensitive).
    # Antes era por `ordem` (1..N do seed) — em celular o seed usou
    # row_number OVER (ORDER BY sku), então os SKUs `dg*` (Fossibot)
    # apareciam antes dos `i*` (Apple), confundindo o operador. Pra mala
    # o modelo já casa razoavelmente com o sku, então alfabético não
    # muda visualmente. Fallback (NULLs no final) via `is_(None)`.
    bases = (await session.execute(
        select(ImportKitBase)
        .where(ImportKitBase.categoria == categoria)
        .order_by(
            ImportKitBase.modelo_bling.is_(None),
            func.lower(ImportKitBase.modelo_bling),
            ImportKitBase.sku_base,
        )
    )).scalars().all()
    marks = (await session.execute(
        select(ImportKitMark).where(ImportKitMark.categoria == categoria)
    )).scalars().all()
    return ImportKitGridOut(
        variations=[ImportKitVariationOut.model_validate(v, from_attributes=True) for v in variations],
        bases=[ImportKitBaseOut.model_validate(b, from_attributes=True) for b in bases],
        marks=[ImportKitMarkOut.model_validate(m, from_attributes=True) for m in marks],
    )


@router.put("/kit/mark", response_model=ImportKitMarkOut | None)
async def toggle_kit_mark(
    body: ImportKitMarkToggle,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("importacao", "edit"))],
) -> ImportKitMarkOut | None:
    """Toggle idempotente. Quando `marked=True` e a mark não existe,
    cria com `bling_sync_status='pending'` e enfileira job ARQ
    `create_bling_kit_for_mark_job` que cria o composto no Bling.
    Retorna a mark criada (com id real) — frontend usa pra atualizar
    o cache otimista, evitando drift entre placeholder 'pending' e
    o id verdadeiro do DB.

    Quando `marked=False`, deleta a mark local e retorna null. NÃO
    apaga o produto no Bling — operação destrutiva fica pra ser feita
    manualmente. A UI mostra warning antes de desmarcar rows com
    bling_product_id.

    Validações estritas (anti-silent-fallback):
      * base + variation devem existir → 404
      * base.categoria == variation.categoria → 422 (cruzamento entre
        mala/celular é bug do cliente, não recupera silenciosamente)
    """
    # Validações antes de qualquer write. Antes o código fazia
    # `categoria = base.categoria if base else 'mala'` — mascara base_id
    # inválido como mark mala, que depois falhava silenciosamente na FK
    # ou criava lixo. Rejeita explicitamente.
    base = await session.get(ImportKitBase, body.base_id)
    if base is None:
        raise HTTPException(404, detail={"code": "kit_base_not_found"})
    variation = await session.get(ImportKitVariation, body.variation_id)
    if variation is None:
        raise HTTPException(404, detail={"code": "kit_variation_not_found"})
    if (base.categoria or "").lower() != (variation.categoria or "").lower():
        raise HTTPException(422, detail={
            "code": "kit_categoria_mismatch",
            "base_categoria": base.categoria,
            "variation_categoria": variation.categoria,
        })

    existing = (await session.execute(
        select(ImportKitMark).where(
            ImportKitMark.base_id == body.base_id,
            ImportKitMark.variation_id == body.variation_id,
        )
    )).scalar_one_or_none()
    if body.marked and existing is None:
        # Mark herda a categoria da base (== da variation, já validado).
        mark = ImportKitMark(
            base_id=body.base_id,
            variation_id=body.variation_id,
            categoria=base.categoria,
            bling_sync_status="pending",
        )
        session.add(mark)
        await session.commit()
        await session.refresh(mark)
        logger.info(
            "kit_mark_created",
            mark_id=str(mark.id), categoria=mark.categoria,
            base_id=str(mark.base_id), variation_id=str(mark.variation_id),
        )
        # Enfileirar criação do composto no Bling (fire-and-forget).
        try:
            pool = await get_arq_ui_pool()
            await pool.enqueue_job("create_bling_kit_for_mark_job", str(mark.id))
        except Exception as e:  # noqa: BLE001
            # Não derruba a UI se o ARQ estiver indisponível — operador
            # pode usar resync depois.
            logger.warning("kit_enqueue_failed", mark_id=str(mark.id), err=str(e)[:200])
        return ImportKitMarkOut.model_validate(mark, from_attributes=True)
    if not body.marked and existing is not None:
        deleted_id = str(existing.id)
        await session.delete(existing)
        await session.commit()
        logger.info("kit_mark_deleted", mark_id=deleted_id, categoria=base.categoria)
        return None
    # No-op: marked=True e já existe, OU marked=False e nada a deletar.
    # Retorna a row atual (se houver) pro frontend manter consistência.
    if existing is not None:
        return ImportKitMarkOut.model_validate(existing, from_attributes=True)
    return None


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
        pool = await get_arq_ui_pool()
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
