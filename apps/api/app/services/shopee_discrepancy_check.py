"""Shopee Discrepancy Check service.

Compares Shopee's actual stock (via API) against the expected stock
in our database (product_links.stock or product.stock). Fixes discrepancies
by pushing the correct stock to Shopee.

This runs as a cron job every 4 hours to catch "phantom stock" issues where
Shopee's stock drifts from what we expect (e.g., due to failed syncs,
Shopee promotions eating stock, or manual edits on Shopee Seller Center).

Flow:
1. For each active Shopee integration:
   a. Get all product_links for that integration
   b. For each link, fetch actual stock from Shopee API
   c. Compare with product.stock (our source of truth from Bling)
   d. If mismatch found, push correct stock to Shopee
2. Log all corrections for audit trail
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session_ctx
from app.models import (
    Integration,
    IntegrationPlatform,
    Product,
    ProductLink,
    SyncLog,
)
from app.security.cipher import decrypt_json, encrypt_json
from app.services.marketplaces.factory import client_for
from app.services.marketplaces.shopee import ShopeeClient
from app.services.marketplaces.base import SyncStatus

logger = structlog.get_logger()

# Max links to check per run to avoid rate limiting
MAX_LINKS_PER_RUN = 200
# Delay between API calls to respect Shopee rate limits
API_DELAY_SECONDS = 0.5


async def run_shopee_discrepancy_check() -> dict[str, Any]:
    """Main entry point for the cron job.

    Returns a summary dict with counts of checked/fixed/errors.
    """
    stats = {"checked": 0, "fixed": 0, "errors": 0, "skipped": 0}

    async with get_session_ctx() as session:
        # Get all active Shopee integrations
        integrations = (
            await session.execute(
                select(Integration).where(
                    Integration.platform == IntegrationPlatform.SHOPEE,
                    Integration.is_active == True,  # noqa: E712
                )
            )
        ).scalars().all()

        for integration in integrations:
            try:
                integration_stats = await _check_integration(
                    session, integration
                )
                stats["checked"] += integration_stats["checked"]
                stats["fixed"] += integration_stats["fixed"]
                stats["errors"] += integration_stats["errors"]
                stats["skipped"] += integration_stats["skipped"]
            except Exception as e:  # noqa: BLE001
                logger.error(
                    "shopee_discrepancy_integration_error",
                    integration_id=str(integration.id),
                    error=str(e)[:500],
                )
                stats["errors"] += 1

    logger.info("shopee_discrepancy_check_complete", **stats)
    return stats


async def _check_integration(
    session: AsyncSession,
    integration: Integration,
) -> dict[str, int]:
    """Check all links for a single Shopee integration."""
    stats = {"checked": 0, "fixed": 0, "errors": 0, "skipped": 0}

    # "Modo férias": não corrige/empurra estoque pra contas pausadas — senão o
    # discrepancy-check reverteria o freeze empurrando o estoque local de volta.
    if integration.vacation_mode:
        stats["skipped_vacation"] = 1
        return stats

    creds = decrypt_json(integration.credentials)

    async def _persist_refresh(new_creds: dict) -> None:
        integration.credentials = encrypt_json(new_creds)
        exp = new_creds.get("expires_at")
        if exp:
            from datetime import datetime as dt
            integration.token_expires_at = dt.fromtimestamp(int(exp), tz=UTC)
        await session.commit()

    client = client_for(
        IntegrationPlatform.SHOPEE, creds, on_token_refresh=_persist_refresh
    )
    if not isinstance(client, ShopeeClient):
        return stats

    # Get product links for this integration
    links = (
        await session.execute(
            select(ProductLink)
            .join(Product, Product.id == ProductLink.product_id)
            .where(
                ProductLink.integration_id == integration.id,
                ProductLink.platform == IntegrationPlatform.SHOPEE,
                ProductLink.last_sync_status != "fatal",
            )
            .limit(MAX_LINKS_PER_RUN)
        )
    ).scalars().all()

    for link in links:
        stats["checked"] += 1
        try:
            # Get the product to know expected stock
            product = (
                await session.execute(
                    select(Product).where(Product.id == link.product_id)
                )
            ).scalar_one_or_none()

            if not product:
                stats["skipped"] += 1
                continue

            expected_stock = product.stock

            # Get actual stock from Shopee
            item_id = int(link.external_id)
            model_id = int(link.variation_id) if link.variation_id else 0
            actual_stock = await client.get_model_stock(item_id, model_id)

            # Compare
            if actual_stock != expected_stock:
                logger.info(
                    "shopee_discrepancy_found",
                    integration_id=str(integration.id),
                    item_id=link.external_id,
                    model_id=model_id,
                    expected=expected_stock,
                    actual=actual_stock,
                )

                # Fix: push correct stock to Shopee
                result = await client.update_stock(link, expected_stock)
                if result.status == SyncStatus.OK:
                    link.stock = expected_stock
                    link.last_synced_at = datetime.now(UTC)
                    stats["fixed"] += 1
                    logger.info(
                        "shopee_discrepancy_fixed",
                        item_id=link.external_id,
                        old_stock=actual_stock,
                        new_stock=expected_stock,
                    )
                else:
                    stats["errors"] += 1
                    logger.warning(
                        "shopee_discrepancy_fix_failed",
                        item_id=link.external_id,
                        error=result.error_code,
                    )

            # Rate limit
            await asyncio.sleep(API_DELAY_SECONDS)

        except Exception as e:  # noqa: BLE001
            stats["errors"] += 1
            logger.warning(
                "shopee_discrepancy_link_error",
                link_id=str(link.id),
                error=str(e)[:300],
            )

    await session.commit()
    return stats
