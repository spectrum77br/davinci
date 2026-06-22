"""Sync diário de `products.bling_cost_price` a partir do Bling.

O endpoint de LISTAGEM (`GET /produtos`, paginado) traz o `precoCusto` real
de cada produto — ao contrário do `GET /produtos/{id}`, que devolve
`precoCusto: null`. Por isso paginamos a lista uma vez e atualizamos
`products.bling_cost_price` em lote (~39 páginas p/ ~3800 produtos), em vez de
1 GET de detalhe por produto.

Importante: os pedidos fazem snapshot de `products.bling_cost_price` quando
entram em situacao=6 (ver `bling_orders._cost_price_by_sku`). Manter esse
custo fresco diariamente mantém o custo carimbado em cada pedido novo atual.

Cobertura: a listagem `/produtos` (sem `criterio`) retorna só os produtos
ATIVOS — que são os únicos que podem virar pedido novo. Produtos inativos
não têm o custo atualizado por aqui (e não precisam). Por isso `seen`
(ativos casados no Bling) costuma ser menor que `total` (linkados no DB).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

import structlog
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BlingOrder,
    ImportProduct,
    Integration,
    IntegrationPlatform,
    Product,
)
from app.security.cipher import decrypt_json, encrypt_json
from app.services.marketplaces.bling import BlingClient

logger = structlog.get_logger()


def _to_decimal(v: object) -> Decimal | None:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return None


async def run_sync_product_bling_costs(session: AsyncSession) -> dict:
    """Atualiza `products.bling_cost_price` de TODOS os produtos linkados ao
    Bling, lendo `precoCusto` da listagem paginada. Retorna um resumo."""
    integ = (
        await session.execute(
            select(Integration)
            .where(Integration.platform == IntegrationPlatform.BLING)
            .limit(1)
        )
    ).scalar_one_or_none()
    if integ is None:
        logger.warning("product_cost_sync_no_integration")
        return {"status": "no_integration"}

    creds = decrypt_json(integ.credentials)

    async def _persist(new_creds: dict) -> None:
        integ.credentials = encrypt_json(new_creds)
        exp = new_creds.get("expires_at")
        if exp:
            integ.token_expires_at = datetime.fromtimestamp(int(exp), tz=UTC)
        await session.commit()

    client = BlingClient(creds, on_token_refresh=_persist, integration_id=integ.id)

    products = (
        await session.execute(
            select(Product).where(Product.bling_product_id.isnot(None))
        )
    ).scalars().all()
    by_id = {p.bling_product_id: p for p in products}

    summary = {
        "total": len(by_id),
        "seen": 0,
        "updated": 0,
        "unchanged": 0,
        "no_cost": 0,
    }

    async for raw in client.list_products():
        pid = raw.get("id")
        prod = by_id.get(pid)
        if prod is None:
            continue
        summary["seen"] += 1
        new_cost = _to_decimal(raw.get("precoCusto"))
        if new_cost is None:
            summary["no_cost"] += 1
        elif prod.bling_cost_price != new_cost:
            prod.bling_cost_price = new_cost
            summary["updated"] += 1
        else:
            summary["unchanged"] += 1
        # Marca o custo como conferido AGORA mesmo quando não mudou — o
        # refresh on-ingest usa essa data pra saber o que está velho.
        if new_cost is not None:
            prod.bling_cost_synced_at = datetime.now(UTC)

    await session.commit()
    logger.info("product_cost_sync_done", **summary)
    return summary


async def run_restamp_order_costs(
    session: AsyncSession, *, window_days: int = 60
) -> dict:
    """Re-carimba `bling_orders.preco_custo` em linhas recentes que ficaram NULL.

    O custo do pedido é um snapshot tirado de `products.bling_cost_price` no
    momento da ingestão (ver `bling_orders._cost_price_by_sku`). SKUs novos
    (ex.: variantes recém-criadas) viram pedido ANTES do sync diário preencher
    o custo do produto, então a linha entra com `preco_custo = NULL` — e o
    caminho de re-ingestão "narrow UPDATE" (`upsert_order`) nunca re-carimba.
    Resultado: a página de margens mostra "—" no Item Custo para sempre.

    Este passo roda logo após `run_sync_product_bling_costs` (custo já fresco)
    e preenche essas linhas com o custo agora disponível, casando por SKU exato
    (`item_codigo` = `products.sku`), restrito aos últimos `window_days` dias de
    ingestão — não reescreve histórico antigo com o custo de hoje. O snapshot
    `verificar_margem` reflete via o cron de 30min que reconstrói da view.
    """
    fresh_cost = (
        select(func.max(Product.bling_cost_price))
        .where(
            Product.sku == BlingOrder.item_codigo,
            Product.bling_cost_price.isnot(None),
        )
        .scalar_subquery()
    )
    cutoff = datetime.now(UTC) - timedelta(days=window_days)
    stmt = (
        update(BlingOrder)
        .where(
            BlingOrder.preco_custo.is_(None),
            BlingOrder.created_at >= cutoff,
            fresh_cost.isnot(None),
        )
        .values(preco_custo=fresh_cost)
        .execution_options(synchronize_session=False)
    )
    result = await session.execute(stmt)
    await session.commit()
    updated = result.rowcount or 0
    logger.info("order_cost_restamp_done", updated=updated, window_days=window_days)
    return {"updated": updated}


async def run_sync_import_bling_costs(session: AsyncSession) -> dict:
    """Propaga o custo fresco do Bling pra `import_products.custo_bling`.

    Fonte = `products.bling_cost_price` (atualizado logo antes por
    `run_sync_product_bling_costs`), casado por SKU. Roda no mesmo cron
    diário, logo depois daquele — assim a aba Importação segue o custo do
    Bling sozinha, do mesmo jeito que a Tabela de Preços (que também
    espelha o custo do Bling, sem digitação manual).

    Só toca linhas linkadas (cujo SKU casa num produto com custo != NULL)
    e cujo valor de fato mudou (IS DISTINCT FROM), pra o `updated_at`
    seguir significativo. `max()` por SKU é defensivo contra SKU duplicado
    em `products` (não deveria existir, mas evita ambiguidade no UPDATE).
    `round(.., 2)` porque `custo_bling` é Numeric(10,2) e `bling_cost_price`
    é Numeric(14,4) — sem arredondar, um custo com >2 decimais re-gravaria
    o mesmo valor toda rodada.
    """
    fresh_cost = (
        select(func.round(func.max(Product.bling_cost_price), 2))
        .where(
            Product.sku == ImportProduct.sku,
            Product.bling_cost_price.isnot(None),
        )
        .scalar_subquery()
    )
    stmt = (
        update(ImportProduct)
        .where(
            fresh_cost.isnot(None),
            ImportProduct.custo_bling.is_distinct_from(fresh_cost),
        )
        .values(custo_bling=fresh_cost, updated_at=func.now())
        .execution_options(synchronize_session=False)
    )
    result = await session.execute(stmt)
    await session.commit()
    updated = result.rowcount or 0
    logger.info("import_cost_sync_done", updated=updated)
    return {"updated": updated}
