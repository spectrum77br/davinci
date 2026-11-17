"""Confirm the Bling source immediately before a Carrefour stock import.

Only GET requests are made to Bling. Missing/ambiguous data is never zero.
The user's initial activation excludes products without a confirmed Bling link.
"""

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID

from sqlalchemy import func, select

from app import db
from app.models import Integration, IntegrationPlatform, Product, ProductLink, SyncLog
from app.models.enums import LinkSyncStatus, SyncLogAction
from app.services import estoque_familia
from app.services.marketplaces.bling import BlingClient
from app.services.sync_orchestrator import SyncOrchestrator


def confirmed_balance(raw: dict, *, sku: str, external_id: str) -> int | None:
    if (
        not isinstance(raw, dict)
        or str(raw.get("id")) != external_id
        or raw.get("codigo") != sku
        or raw.get("situacao") != "A"
    ):
        return None
    stock = raw.get("estoque")
    if not isinstance(stock, dict):
        return None
    value = stock.get("saldoVirtualTotal")
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number != number.to_integral_value():
            return None
        if abs(number) > 2147483647:
            return None
        return max(0, int(number))
    except (InvalidOperation, ValueError, TypeError):
        return None


async def refresh_carrefour_stock_target(link_id: UUID) -> int | None:
    async with db.SessionLocal() as session:
        target = await session.get(ProductLink, link_id)
        if target is None or target.platform != IntegrationPlatform.CARREFOUR:
            return None
        product = await session.get(Product, target.product_id)
        if product is None or target.external_sku != product.sku:
            return None
        products = [product]
        if estoque_familia.familia_ligada(product.sku):
            products = list(
                (
                    await session.scalars(
                        select(Product).where(
                            func.lower(Product.sku).in_(estoque_familia.irmaos(product.sku)),
                            Product.situacao == "A",
                        )
                    )
                ).all()
            )
            if product.id not in {p.id for p in products}:
                products.append(product)
        if len({p.sku.casefold() for p in products}) != len(products):
            return None
        orchestrator = SyncOrchestrator(session, user_id=target.user_id)
        confirmed = []
        for source_product in products:
            links = list(
                (
                    await session.scalars(
                        select(ProductLink).where(
                            ProductLink.product_id == source_product.id,
                            ProductLink.platform == IntegrationPlatform.BLING,
                        )
                    )
                ).all()
            )
            if len(links) != 1:
                return None
            source = links[0]
            if source.external_sku and source.external_sku != source_product.sku:
                return None
            integration = await session.get(Integration, source.integration_id)
            if (
                integration is None
                or integration.platform != IntegrationPlatform.BLING
                or integration.status != "active"
                or integration.archived_at is not None
            ):
                return None
            try:
                client = await orchestrator._client(integration)
                if not isinstance(client, BlingClient):
                    return None
                raw = await client.get_product(int(source.external_id))
            except Exception:
                # No credential-bearing transport errors enter logs or UI.
                return None
            balance = confirmed_balance(
                raw,
                sku=source_product.sku,
                external_id=source.external_id,
            )
            if balance is None:
                return None
            confirmed.append((source_product, source, balance))
        now = datetime.now(UTC)
        for source_product, source, balance in confirmed:
            before = source_product.stock
            source_product.stock = source.stock = balance
            source.last_sync_at, source.last_sync_status, source.last_error = (
                now,
                LinkSyncStatus.OK,
                None,
            )
            session.add(
                SyncLog(
                    user_id=source.user_id,
                    product_id=source_product.id,
                    product_link_id=source.id,
                    integration_id=source.integration_id,
                    store_id=source.store_id,
                    platform=IntegrationPlatform.BLING,
                    action=SyncLogAction.REFRESH_BLING,
                    status=LinkSyncStatus.OK,
                    qty_before=before,
                    qty_after=balance,
                    payload={"source": "bling_refresh", "for": "carrefour_stock_write"},
                )
            )
        await session.flush()
        result = await estoque_familia.saldo_publicavel(session, product)
        await session.commit()
        return result
