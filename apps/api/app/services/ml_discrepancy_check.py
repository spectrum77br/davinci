"""Mercado Livre Discrepancy Check service.

Compares ML's actual stock (via API) against the expected stock
in our database. Fixes discrepancies by pushing the correct stock to ML.

Similar to shopee_discrepancy_check but for Mercado Livre.
ML API uses GET /items/{id} to read stock, and PUT /items/{id} to update.

Flow:
1. For each active ML integration:
   a. Get all product_links for that integration
   b. For each link, fetch actual stock from ML API
   c. Compare with product.stock (our source of truth from Bling)
   d. If mismatch found, push correct stock to ML
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
)
from app.security.cipher import decrypt_json, encrypt_json
from app.services.marketplaces.factory import client_for
from app.services.marketplaces.ml import MercadoLivreClient
from app.services.marketplaces.base import SyncStatus

logger = structlog.get_logger()

# Max links to check per run
MAX_LINKS_PER_RUN = 200
# Delay between API calls to respect ML rate limits
API_DELAY_SECONDS = 0.3


async def run_ml_discrepancy_check() -> dict[str, Any]:
    """Main entry point for the cron job.

    Returns a summary dict with counts of checked/fixed/errors.
    """
    stats = {"checked": 0, "fixed": 0, "errors": 0, "skipped": 0}

    async with get_session_ctx() as session:
        # Get all active ML integrations
        integrations = (
            await session.execute(
                select(Integration).where(
                    Integration.platform == IntegrationPlatform.ML,
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
                    "ml_discrepancy_integration_error",
                    integration_id=str(integration.id),
                    error=str(e)[:500],
                )
                stats["errors"] += 1

    logger.info("ml_discrepancy_check_complete", **stats)
    return stats


async def _check_integration(
    session: AsyncSession,
    integration: Integration,
) -> dict[str, int]:
    """Check all links for a single ML integration."""
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
            integration.token_expires_at = datetime.fromtimestamp(int(exp), tz=UTC)
        await session.commit()

    client = client_for(
        IntegrationPlatform.ML, creds, on_token_refresh=_persist_refresh
    )
    if not isinstance(client, MercadoLivreClient):
        return stats

    # Get product links for this integration
    links = (
        await session.execute(
            select(ProductLink)
            .join(Product, Product.id == ProductLink.product_id)
            .where(
                ProductLink.integration_id == integration.id,
                ProductLink.platform == IntegrationPlatform.ML,
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

            # Get actual stock from ML
            actual_stock = await _get_ml_stock(client, link.external_id, link.variation_id)
            if actual_stock is None:
                stats["skipped"] += 1
                continue

            # Compare
            if actual_stock != expected_stock:
                logger.info(
                    "ml_discrepancy_found",
                    integration_id=str(integration.id),
                    item_id=link.external_id,
                    variation_id=link.variation_id,
                    expected=expected_stock,
                    actual=actual_stock,
                )

                # Fix: push correct stock to ML
                result = await client.update_stock(link, expected_stock)
                if result.status == SyncStatus.OK:
                    link.stock = expected_stock
                    link.last_synced_at = datetime.now(UTC)
                    stats["fixed"] += 1
                    logger.info(
                        "ml_discrepancy_fixed",
                        item_id=link.external_id,
                        old_stock=actual_stock,
                        new_stock=expected_stock,
                    )
                else:
                    stats["errors"] += 1
                    logger.warning(
                        "ml_discrepancy_fix_failed",
                        item_id=link.external_id,
                        error=result.error_code,
                    )

            # Rate limit
            await asyncio.sleep(API_DELAY_SECONDS)

        except Exception as e:  # noqa: BLE001
            stats["errors"] += 1
            logger.warning(
                "ml_discrepancy_link_error",
                link_id=str(link.id),
                error=str(e)[:300],
            )

    await session.commit()
    return stats


async def _get_ml_stock(
    client: MercadoLivreClient,
    item_id: str,
    variation_id: str | None,
) -> int | None:
    """Fetch current stock from ML for a given item/variation.

    Uses GET /items/{id} which returns available_quantity at item level,
    or variations[].available_quantity for variation-level stock.
    """
    try:
        r = await client._request("GET", f"/items/{item_id}")
        if r.status_code != 200:
            return None
        data = r.json() or {}

        if variation_id:
            # Look for the specific variation
            for v in data.get("variations") or []:
                if str(v.get("id")) == str(variation_id):
                    return int(v.get("available_quantity") or 0)
            # Variation not found, return item-level
            return int(data.get("available_quantity") or 0)
        else:
            return int(data.get("available_quantity") or 0)
    except Exception:  # noqa: BLE001
        return None
