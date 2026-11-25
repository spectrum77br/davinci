"""O Bling trocou o código (SKU) de um produto: o DaVinci acompanha.

Antes isto morava inteiro no webhook do Bling (routers/webhooks.py, "Bling renomeou o código do
produto", caso a017.pi → a017.cd, 13/ago/2026). Agora é compartilhado: o webhook continua igual, e
o "Trocar a tag" do Produtos 2 (M-46) passa pelo MESMO caminho logo depois de trocar o código no
Bling, sem esperar o webhook chegar.
"""

from __future__ import annotations

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import IntegrationPlatform, Product, ProductLink

logger = structlog.get_logger()


async def resolver_produto(
    session: AsyncSession,
    *,
    sku: str | None,
    bling_product_id: int | None,
) -> Product | None:
    """O produto do DaVinci de um aviso do Bling: pelo SKU; senão pelo id do Bling; senão pelo
    vínculo do Bling (o id fica no `external_id`)."""
    if sku:
        prod = (
            await session.execute(select(Product).where(Product.sku == sku).limit(1))
        ).scalar_one_or_none()
        if prod is not None:
            return prod
    if bling_product_id is not None:
        prod = (
            await session.execute(
                select(Product).where(Product.bling_product_id == bling_product_id).limit(1)
            )
        ).scalar_one_or_none()
        if prod is not None:
            return prod
        link = (
            await session.execute(
                select(ProductLink)
                .where(
                    ProductLink.platform == IntegrationPlatform.BLING,
                    ProductLink.external_id == str(bling_product_id),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if link is not None:
            return await session.get(Product, link.product_id)
    return None


def aplicar_codigo_do_bling(
    product: Product | None,
    sku: str | None,
    bling_product_id: int | None,
    *,
    evento: str = "bling_webhook_sku_renamed",
) -> bool:
    """Bling renomeou o código do produto? Quando o produto foi achado pelo id do Bling ou pelo
    vínculo (não pelo SKU), o código que o Bling manda é o NOVO e a linha local ainda tem o
    antigo — espelha aqui, senão o SKU velho vira fantasma duplicado no Controle de Estoque e o
    novo "não existe" nas devoluções. Só muda `products.sku` (quem grava é quem chama).
    Devolve True quando trocou."""
    if (
        product is not None
        and sku
        and bling_product_id is not None
        and (product.sku or "").strip() != sku
    ):
        logger.info(
            evento,
            old=product.sku,
            new=sku,
            bling_product_id=bling_product_id,
        )
        product.sku = sku
        return True
    return False
