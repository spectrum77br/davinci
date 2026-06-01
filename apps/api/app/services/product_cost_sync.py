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

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Integration, IntegrationPlatform, Product
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

    await session.commit()
    logger.info("product_cost_sync_done", **summary)
    return summary
