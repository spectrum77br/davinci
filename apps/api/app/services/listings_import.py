"""Listings import + auto-link by SKU (Fase 8).

Two entry points:
- `run_import_listings`: marketplace-scoped fetch via the marketplace client's
  `list_listings()` async iterator, upsert into `davinci.listings` keyed by
  `(integration_id, external_id)`.
- `run_auto_import_link`: scan listings with `product_id IS NULL` and a
  non-blank SKU, attach the product whose `(user_id, sku)` matches.

Both are designed to be idempotent — re-running a recent import refreshes
title/price/stock/status without orphaning rows; auto-link ignores listings
already linked.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import text as sa_text

from app.models import (
    BackgroundJob,
    BackgroundJobStatus,
    Integration,
    IntegrationPlatform,
    Listing,
    ListingStatus,
    Product,
)
from app.security.cipher import decrypt_json, encrypt_json
from app.services.marketplaces.ml import MercadoLivreClient
from app.services.marketplaces.shopee import ShopeeClient

logger = structlog.get_logger()


def _now() -> datetime:
    return datetime.now(UTC)


async def _heartbeat(session: AsyncSession, job: BackgroundJob) -> None:
    job.last_heartbeat_at = _now()
    await session.commit()


def _coerce_status(raw: str | None) -> ListingStatus:
    if not raw:
        return ListingStatus.ACTIVE
    try:
        return ListingStatus(raw)
    except ValueError:
        return ListingStatus.INACTIVE


def _client_for_listings(session: AsyncSession, integ: Integration):
    """Returns a marketplace client that supports `list_listings` or None.

    Bling and Amazon are intentionally not wired here — Bling listings are
    materialized from the `products` table, and Amazon SP-API listings retrieval
    is out of scope for this phase."""
    creds = decrypt_json(integ.credentials)

    async def on_refresh(new_creds: dict) -> None:
        integ.credentials = encrypt_json(new_creds)
        exp = new_creds.get("expires_at")
        if exp:
            integ.token_expires_at = datetime.fromtimestamp(int(exp), tz=UTC)
        await session.commit()

    if integ.platform == IntegrationPlatform.SHOPEE:
        return ShopeeClient(creds, on_token_refresh=on_refresh)
    if integ.platform == IntegrationPlatform.ML:
        return MercadoLivreClient(creds, on_token_refresh=on_refresh)
    return None


async def _upsert_listing(
    session: AsyncSession,
    *,
    user_id: UUID,
    integ: Integration,
    norm: dict[str, Any],
) -> str:
    """Returns 'created' | 'updated' | 'skipped'."""
    external_id = (norm.get("external_id") or "").strip()
    if not external_id:
        return "skipped"

    existing = (
        await session.execute(
            select(Listing).where(
                and_(
                    Listing.integration_id == integ.id,
                    Listing.external_id == external_id,
                )
            )
        )
    ).scalar_one_or_none()

    sku = norm.get("sku")
    title = (norm.get("title") or "").strip() or external_id
    status = _coerce_status(norm.get("status"))

    if existing is None:
        row = Listing(
            user_id=user_id,
            integration_id=integ.id,
            platform=integ.platform,
            external_id=external_id,
            sku=sku,
            listing_type=norm.get("listing_type"),
            title=title,
            description=norm.get("description"),
            price=norm.get("price"),
            stock=norm.get("stock"),
            status=status,
            category=norm.get("category"),
            thumbnail_url=norm.get("thumbnail_url"),
            raw_data=norm.get("raw") or {},
            imported_at=_now(),
        )
        session.add(row)
        return "created"

    existing.sku = sku
    existing.listing_type = norm.get("listing_type")
    existing.title = title
    existing.description = norm.get("description")
    existing.price = norm.get("price")
    existing.stock = norm.get("stock")
    existing.status = status
    existing.category = norm.get("category")
    existing.thumbnail_url = norm.get("thumbnail_url")
    existing.raw_data = norm.get("raw") or {}
    existing.imported_at = _now()
    return "updated"


async def _create_product_links_for_matched(session: AsyncSession) -> int:
    """Promote matched `listings` into `product_links` rows.

    A listing carries the (product, integration, external_id, sku, title,
    stock) tuple. The orchestrator only pushes stock per `product_link`, so
    a listing matched via `_link_by_sku` is invisible to sync_all until a
    corresponding row exists in `product_links`. Skip rows that already
    exist for (integration_id, platform, external_id).
    """
    sql = sa_text(
        """
        INSERT INTO davinci.product_links (
            id, user_id, product_id, integration_id, store_id, platform,
            external_id, external_sku, listing_title, listing_type, stock, price,
            last_sync_status, last_sync_at, created_at, updated_at
        )
        SELECT
            gen_random_uuid(), l.user_id, l.product_id, l.integration_id,
            i.store_id, l.platform, l.external_id, l.sku, l.title,
            l.listing_type, l.stock, NULL, 'pending', NULL, NOW(), NOW()
        FROM davinci.listings l
        JOIN davinci.integrations i ON i.id = l.integration_id
        WHERE l.product_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM davinci.product_links pl
              WHERE pl.integration_id = l.integration_id
                AND pl.platform = l.platform
                AND pl.external_id = l.external_id
          )
        """
    )
    r = await session.execute(sql)
    return r.rowcount or 0


async def _link_by_sku(session: AsyncSession) -> int:
    """Single-pass auto-link: set listings.product_id where sku matches.

    Uses a single UPDATE gated by an EXISTS so the rowcount only counts
    listings that actually got linked (otherwise listings with no matching
    product would be counted as updated too, even though `product_id` ends
    up unchanged)."""
    matching_product_subq = (
        select(Product.id)
        .where(Product.sku == Listing.sku)
        .correlate(Listing)
    )
    res = await session.execute(
        update(Listing)
        .where(
            and_(
                Listing.product_id.is_(None),
                Listing.sku.is_not(None),
                func.length(func.trim(Listing.sku)) > 0,
                matching_product_subq.exists(),
            )
        )
        .values(product_id=matching_product_subq.scalar_subquery())
    )
    return res.rowcount or 0


async def run_import_listings(
    session: AsyncSession,
    *,
    job_id: UUID,
    user_id: UUID,
    integration_id: UUID,
    max_pages: int | None = None,
) -> None:
    job = await session.get(BackgroundJob, job_id)
    if job is None:
        logger.error("import_listings_job_missing", job_id=str(job_id))
        return

    job.status = BackgroundJobStatus.RUNNING
    job.started_at = _now()
    job.last_heartbeat_at = _now()
    await session.commit()

    integ = await session.get(Integration, integration_id)
    if integ is None:
        job.status = BackgroundJobStatus.FAILED
        job.error = "integration_not_found"
        job.finished_at = _now()
        await session.commit()
        return

    client = _client_for_listings(session, integ)
    if client is None:
        job.status = BackgroundJobStatus.FAILED
        job.error = f"platform_not_supported:{integ.platform.value}"
        job.finished_at = _now()
        await session.commit()
        return

    summary = {"created": 0, "updated": 0, "skipped": 0, "linked": 0, "product_links": 0}
    try:
        kwargs: dict[str, Any] = {}
        if max_pages is not None:
            kwargs["max_pages"] = max_pages
        async for norm in client.list_listings(**kwargs):
            outcome = await _upsert_listing(
                session, user_id=user_id, integ=integ, norm=norm
            )
            summary[outcome] += 1
            job.processed = (job.processed or 0) + 1
            if job.processed % 25 == 0:
                await _heartbeat(session, job)

        # Auto-link in the same job so the user sees linked counts up-front.
        summary["linked"] = await _link_by_sku(session)
        summary["product_links"] = await _create_product_links_for_matched(session)

        job.total = (
            summary["created"] + summary["updated"] + summary["skipped"]
        )
        job.status = BackgroundJobStatus.SUCCEEDED
        job.result = summary
    except Exception as e:  # noqa: BLE001
        logger.exception("import_listings_failed", job_id=str(job_id))
        job.status = BackgroundJobStatus.FAILED
        job.error = f"{type(e).__name__}: {e}"[:1000]
    finally:
        job.finished_at = _now()
        await session.commit()


async def run_auto_import_link(session: AsyncSession) -> dict[str, int]:
    """Cron entrypoint: single-tenant CRM — one global pass over all
    unlinked listings."""
    total_linked = await _link_by_sku(session)
    total_product_links = await _create_product_links_for_matched(session)
    await session.commit()
    return {
        "linked": total_linked,
        "product_links": total_product_links,
    }
