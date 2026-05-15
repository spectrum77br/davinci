"""Auto-link orchestrator (Fase 3 scope).

For each user's Bling integration, scan products imported from Bling and ensure
there is a `product_links` row with platform=bling pointing to the same Bling
product (self-link). The Bling integration links every product to its origin
channel inside Bling and keeps the `store_id` derivation (integration -> store)
consistent with how Fase 4 will materialize ML/Shopee/Amazon links.

Non-Bling integrations are recorded in `details` as `no_adapter_yet` so the
job log is honest about what was skipped. Fase 4 fills those adapters.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import and_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BackgroundJob,
    BackgroundJobStatus,
    Integration,
    IntegrationPlatform,
    LinkSyncStatus,
    Product,
    ProductLink,
)
from app.security.cipher import decrypt_json, encrypt_json
from app.services.marketplaces.amazon import AmazonClient
from app.services.marketplaces.ml import MercadoLivreClient
from app.services.marketplaces.shopee import ShopeeClient
from app.services.marketplaces.tiktok import TikTokClient

logger = structlog.get_logger()


def _now() -> datetime:
    return datetime.now(UTC)


def _loop_now() -> float:
    return time.monotonic()


async def _heartbeat(session: AsyncSession, job: BackgroundJob) -> None:
    job.last_heartbeat_at = _now()
    await session.commit()


async def _append_detail(session: AsyncSession, job: BackgroundJob, entry: dict[str, Any]) -> None:
    entry = {"at": _now().isoformat(), **entry}
    job.details = [*(job.details or []), entry]
    await session.commit()


async def _link_bling_integration(
    session: AsyncSession,
    job: BackgroundJob,
    integ: Integration,
) -> tuple[int, int, str | None]:
    """Returns (created, already_present, error)."""
    products = (
        await session.execute(
            select(Product).where(Product.bling_product_id.is_not(None))
        )
    ).scalars().all()

    existing_links = (
        await session.execute(
            select(ProductLink).where(
                and_(
                    ProductLink.integration_id == integ.id,
                    ProductLink.platform == IntegrationPlatform.BLING,
                )
            )
        )
    ).scalars().all()
    by_external = {
        (link.external_id, link.product_id): link for link in existing_links
    }

    created = 0
    already = 0
    for p in products:
        key = (str(p.bling_product_id), p.id)
        if key in by_external:
            already += 1
            continue
        link = ProductLink(
            user_id=integ.user_id,
            product_id=p.id,
            integration_id=integ.id,
            store_id=integ.store_id,
            platform=IntegrationPlatform.BLING,
            external_id=str(p.bling_product_id),
            external_sku=p.sku,
            listing_title=p.name,
            stock=p.stock,
            price=p.price,
            last_sync_status=LinkSyncStatus.OK,
            last_sync_at=_now(),
        )
        session.add(link)
        created += 1
        job.processed = (job.processed or 0) + 1
        if created % 25 == 0:
            await _heartbeat(session, job)
    return created, already, None


async def _link_tiktok_integration(
    session: AsyncSession,
    job: BackgroundJob,
    integ: Integration,
) -> tuple[int, int, int, str | None]:
    """Returns (created, already_present, not_found, error).

    Iterates `client.search_products` in pages of 100, matching each TikTok
    SKU's `seller_sku` against the user's `products.sku` (case-insensitive).
    For every match without an existing `product_links` row, creates one
    with `platform=tiktok` and the TikTok product/sku ids in
    `external_id` / `variation_id`.
    """
    # Build case-insensitive sku → product index for fast match.
    products = (await session.execute(select(Product))).scalars().all()
    by_sku: dict[str, Product] = {}
    for p in products:
        sk = (p.sku or "").strip().lower()
        if sk:
            by_sku[sk] = p

    existing_keys: set[tuple[UUID, str, str]] = set()
    for link in (
        await session.execute(
            select(ProductLink).where(
                and_(
                    ProductLink.integration_id == integ.id,
                    ProductLink.platform == IntegrationPlatform.TIKTOK,
                )
            )
        )
    ).scalars().all():
        existing_keys.add(
            (link.product_id, link.external_id or "", link.variation_id or "")
        )

    creds = decrypt_json(integ.credentials) if integ.credentials else {}

    async def _persist_refresh(new_creds: dict) -> None:
        integ.credentials = encrypt_json(new_creds)
        await session.commit()

    client = TikTokClient(creds, on_token_refresh=_persist_refresh)

    # SSH parity: MAX_PAGES=20 (caps at 2000 products) and a 2-minute wall
    # timeout per account so a slow/wedged search can't block the auto-link
    # job indefinitely.
    MAX_PAGES = 20
    TIMEOUT_SECONDS = 120.0
    started_at = _loop_now()

    created = 0
    already = 0
    not_found = 0
    error: str | None = None
    page_token: str | None = None
    page_idx = 0
    while True:
        if page_idx >= MAX_PAGES:
            logger.warning(
                "auto_link_tiktok_max_pages",
                integration_id=str(integ.id),
                pages=page_idx,
            )
            break
        if _loop_now() - started_at > TIMEOUT_SECONDS:
            logger.warning(
                "auto_link_tiktok_timeout",
                integration_id=str(integ.id),
                elapsed=_loop_now() - started_at,
            )
            error = error or "timeout"
            break
        page_idx += 1
        try:
            tk_products, page_token = await client.search_products(
                page_size=100, page_token=page_token
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "auto_link_tiktok_search_failed",
                integration_id=str(integ.id),
                page=page_idx,
                err=str(e)[:200],
            )
            error = f"search_failed: {str(e)[:200]}"
            break
        if not tk_products:
            break
        for tp in tk_products:
            product_id = (tp.get("product_id") or "").strip()
            title = tp.get("title")
            for sku in tp.get("skus") or []:
                seller_sku = (sku.get("seller_sku") or "").strip()
                sku_id = (sku.get("id") or "").strip()
                if not seller_sku or not sku_id:
                    continue
                local = by_sku.get(seller_sku.lower())
                if local is None:
                    not_found += 1
                    continue
                key = (local.id, product_id, sku_id)
                if key in existing_keys:
                    already += 1
                    continue
                session.add(
                    ProductLink(
                        user_id=integ.user_id,
                        product_id=local.id,
                        integration_id=integ.id,
                        store_id=integ.store_id,
                        platform=IntegrationPlatform.TIKTOK,
                        external_id=product_id,
                        variation_id=sku_id,
                        external_sku=seller_sku,
                        listing_title=title,
                        stock=sku.get("stock"),
                        last_sync_status=LinkSyncStatus.OK,
                        last_sync_at=_now(),
                    )
                )
                existing_keys.add(key)
                created += 1
                job.processed = (job.processed or 0) + 1
                if created % 25 == 0:
                    await _heartbeat(session, job)
        if not page_token:
            break

    await session.commit()
    return created, already, not_found, error


async def _safe_flush_batch(
    session: AsyncSession,
    pending: list[ProductLink],
) -> tuple[int, int]:
    """Flush `pending` rows inside a savepoint. On IntegrityError (FK race
    with a concurrent product delete, or a UniqueViolation we missed in our
    in-memory dedup), fall back to per-row savepoints so good rows still
    land. Returns (committed, skipped).
    """
    if not pending:
        return 0, 0
    sp = await session.begin_nested()
    try:
        session.add_all(pending)
        await session.flush()
        await sp.commit()
        return len(pending), 0
    except IntegrityError:
        await sp.rollback()
    committed = 0
    skipped = 0
    for link in pending:
        sp2 = await session.begin_nested()
        try:
            session.add(link)
            await session.flush()
            await sp2.commit()
            committed += 1
        except IntegrityError as e:
            await sp2.rollback()
            skipped += 1
            logger.warning(
                "auto_link_row_skipped",
                err=str(e)[:200],
                product_id=str(link.product_id),
                external_id=link.external_id,
                variation_id=link.variation_id,
            )
    return committed, skipped


async def _link_via_listings(
    session: AsyncSession,
    job: BackgroundJob,
    integ: Integration,
    client,
    platform: IntegrationPlatform,
) -> tuple[int, int, int, str | None]:
    """Generic adapter: walk `client.list_listings()` (which already yields
    normalized {external_id, sku, title, listing_type, …} dicts for ML and
    Shopee) and create `product_links` for every listing whose SKU matches
    a row in `products`. Returns (created, already_present, not_found, error).
    """
    products = (await session.execute(select(Product))).scalars().all()
    by_sku: dict[str, Product] = {}
    for p in products:
        sk = (p.sku or "").strip().lower()
        if sk:
            by_sku[sk] = p

    # Dedup key matches the DB unique constraint (uq_product_links_identity):
    # (user_id, platform, integration_id, external_id, COALESCE(variation_id, '')).
    # We already filter existing_keys by integration_id below, so we just key
    # the in-memory set on (external_id, variation_id). Including product_id
    # would allow two DaVinci products that share a SKU to both queue a link
    # for the same marketplace listing — and the DB then 23505s out.
    existing_keys: set[tuple[str, str]] = set()
    for link in (
        await session.execute(
            select(ProductLink).where(
                and_(
                    ProductLink.integration_id == integ.id,
                    ProductLink.platform == platform,
                )
            )
        )
    ).scalars().all():
        existing_keys.add(
            (link.external_id or "", link.variation_id or "")
        )

    pending: list[ProductLink] = []
    created = 0
    skipped = 0
    already = 0
    not_found = 0
    error: str | None = None

    async def _flush() -> None:
        nonlocal pending, created, skipped
        if not pending:
            return
        c, s = await _safe_flush_batch(session, pending)
        created += c
        skipped += s
        pending = []
        await _heartbeat(session, job)

    try:
        async for listing in client.list_listings():
            sku = (listing.get("sku") or "").strip()
            external_id = (listing.get("external_id") or "").strip()
            variation_id = (listing.get("variation_id") or "").strip() or None
            if not external_id:
                continue
            if not sku:
                not_found += 1
                continue
            local = by_sku.get(sku.lower())
            if local is None:
                not_found += 1
                continue
            key = (external_id, variation_id or "")
            if key in existing_keys:
                already += 1
                continue
            pending.append(
                ProductLink(
                    user_id=integ.user_id,
                    product_id=local.id,
                    integration_id=integ.id,
                    store_id=integ.store_id,
                    platform=platform,
                    external_id=external_id,
                    variation_id=variation_id,
                    external_sku=sku,
                    listing_title=listing.get("title"),
                    listing_type=listing.get("listing_type"),
                    stock=listing.get("stock"),
                    last_sync_status=LinkSyncStatus.OK,
                    last_sync_at=_now(),
                )
            )
            existing_keys.add(key)
            job.processed = (job.processed or 0) + 1
            if len(pending) >= 100:
                await _flush()
        await _flush()
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "auto_link_listings_failed",
            integration_id=str(integ.id),
            platform=platform.value,
            err=str(e)[:200],
        )
        error = f"listings_failed: {str(e)[:200]}"

    await session.commit()
    if skipped:
        logger.info(
            "auto_link_skipped_rows",
            integration_id=str(integ.id),
            platform=platform.value,
            skipped=skipped,
        )
    return created, already, not_found, error


def _ml_client_for(integ: Integration, session: AsyncSession) -> MercadoLivreClient:
    creds = decrypt_json(integ.credentials) if integ.credentials else {}

    async def _persist_refresh(new_creds: dict) -> None:
        integ.credentials = encrypt_json(new_creds)
        await session.commit()

    return MercadoLivreClient(creds, on_token_refresh=_persist_refresh)


def _shopee_client_for(integ: Integration, session: AsyncSession) -> ShopeeClient:
    creds = decrypt_json(integ.credentials) if integ.credentials else {}

    async def _persist_refresh(new_creds: dict) -> None:
        integ.credentials = encrypt_json(new_creds)
        await session.commit()

    return ShopeeClient(creds, on_token_refresh=_persist_refresh)


def _amazon_client_for(integ: Integration, session: AsyncSession) -> AmazonClient:
    creds = decrypt_json(integ.credentials) if integ.credentials else {}

    async def _persist_refresh(new_creds: dict) -> None:
        integ.credentials = encrypt_json(new_creds)
        await session.commit()

    return AmazonClient(creds, on_token_refresh=_persist_refresh)


async def _link_amazon_integration(
    session: AsyncSession,
    job: BackgroundJob,
    integ: Integration,
) -> tuple[int, int, int, str | None]:
    """Amazon-specific adapter: pulls the GET_MERCHANT_LISTINGS_ALL_DATA
    report (TSV via Reports API), matches by seller-SKU, and bulk-inserts
    product_links in chunks of 100 (mirroring SSH's createProductLinksBulk).

    Dedup key INCLUDES `integration_id` because the same SKU can exist in
    multiple Amazon seller accounts (e.g., MFN + FBA, or two regions).

    Returns (created, already_present, not_found, error).
    """
    client = _amazon_client_for(integ, session)

    products = (await session.execute(select(Product))).scalars().all()
    by_sku: dict[str, Product] = {}
    for p in products:
        sk = (p.sku or "").strip().lower()
        if sk:
            by_sku[sk] = p

    # Same rule as _link_via_listings: dedup on (external_id, variation_id)
    # since the DB unique constraint excludes product_id. existing_keys is
    # already scoped to this integration via the WHERE clause below.
    existing_keys: set[tuple[str, str]] = set()
    for link in (
        await session.execute(
            select(ProductLink).where(
                and_(
                    ProductLink.integration_id == integ.id,
                    ProductLink.platform == IntegrationPlatform.AMAZON,
                )
            )
        )
    ).scalars().all():
        existing_keys.add(
            (link.external_id or "", link.variation_id or "")
        )

    pending: list[ProductLink] = []
    created = 0
    skipped = 0
    already = 0
    not_found = 0
    error: str | None = None

    async def _flush() -> None:
        nonlocal pending, created, skipped
        if not pending:
            return
        c, s = await _safe_flush_batch(session, pending)
        created += c
        skipped += s
        pending = []
        await _heartbeat(session, job)

    try:
        async for listing in client.list_listings():
            sku = (listing.get("sku") or "").strip()
            external_id = (listing.get("external_id") or "").strip()
            variation_id = (listing.get("variation_id") or "").strip() or None
            if not external_id or not sku:
                not_found += 1
                continue
            local = by_sku.get(sku.lower())
            if local is None:
                not_found += 1
                continue
            key = (external_id, variation_id or "")
            if key in existing_keys:
                already += 1
                continue
            pending.append(
                ProductLink(
                    user_id=integ.user_id,
                    product_id=local.id,
                    integration_id=integ.id,
                    store_id=integ.store_id,
                    platform=IntegrationPlatform.AMAZON,
                    external_id=external_id,
                    variation_id=variation_id,
                    external_sku=listing.get("external_sku") or sku,
                    listing_title=listing.get("title"),
                    listing_type=listing.get("listing_type"),
                    stock=listing.get("stock"),
                    last_sync_status=LinkSyncStatus.OK,
                    last_sync_at=_now(),
                )
            )
            existing_keys.add(key)
            if len(pending) >= 100:
                await _flush()
        await _flush()
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "auto_link_amazon_failed",
            integration_id=str(integ.id),
            err=str(e)[:300],
        )
        error = f"amazon_failed: {str(e)[:300]}"

    await session.commit()
    if skipped:
        logger.info(
            "auto_link_skipped_rows",
            integration_id=str(integ.id),
            platform="amazon",
            skipped=skipped,
        )
    return created, already, not_found, error


async def run_auto_link(
    session: AsyncSession,
    *,
    job_id: UUID,
    integration_ids: list[UUID] | None,
) -> None:
    job = (
        await session.execute(select(BackgroundJob).where(BackgroundJob.id == job_id))
    ).scalar_one_or_none()
    if job is None:
        logger.error("auto_link_job_missing", job_id=str(job_id))
        return

    job.status = BackgroundJobStatus.RUNNING
    job.started_at = _now()
    job.last_heartbeat_at = _now()

    stmt = select(Integration)
    if integration_ids:
        stmt = stmt.where(Integration.id.in_(integration_ids))
    integrations = (await session.execute(stmt)).scalars().all()

    job.total = sum(1 for _ in integrations) or 1
    await session.commit()

    summary = {
        "created": 0,
        "already_present": 0,
        "skipped": 0,
        "not_found": 0,
        "failed_integrations": 0,
        "ok_integrations": 0,
    }

    async def _record(integ: Integration, *, created: int, already: int,
                      not_found: int, error: str | None) -> None:
        summary["created"] += created
        summary["already_present"] += already
        summary["not_found"] += not_found
        if error:
            summary["failed_integrations"] += 1
        else:
            summary["ok_integrations"] += 1
        await _append_detail(
            session,
            job,
            {
                "integration_id": str(integ.id),
                "integration_name": integ.name,
                "platform": integ.platform.value,
                "result": "failed" if error else "ok",
                "created": created,
                "already_present": already,
                "not_found": not_found,
                "error": error,
            },
        )

    try:
        for integ in integrations:
            await _heartbeat(session, job)
            if integ.platform == IntegrationPlatform.BLING:
                created, already, error = await _link_bling_integration(
                    session, job, integ
                )
                await _record(
                    integ, created=created, already=already, not_found=0, error=error
                )
            elif integ.platform == IntegrationPlatform.TIKTOK:
                created, already, not_found, error = await _link_tiktok_integration(
                    session, job, integ
                )
                await _record(
                    integ, created=created, already=already,
                    not_found=not_found, error=error,
                )
            elif integ.platform == IntegrationPlatform.ML:
                client = _ml_client_for(integ, session)
                created, already, not_found, error = await _link_via_listings(
                    session, job, integ, client, IntegrationPlatform.ML
                )
                await _record(
                    integ, created=created, already=already,
                    not_found=not_found, error=error,
                )
            elif integ.platform == IntegrationPlatform.SHOPEE:
                client = _shopee_client_for(integ, session)
                created, already, not_found, error = await _link_via_listings(
                    session, job, integ, client, IntegrationPlatform.SHOPEE
                )
                await _record(
                    integ, created=created, already=already,
                    not_found=not_found, error=error,
                )
            elif integ.platform == IntegrationPlatform.AMAZON:
                created, already, not_found, error = await _link_amazon_integration(
                    session, job, integ
                )
                await _record(
                    integ, created=created, already=already,
                    not_found=not_found, error=error,
                )
            else:
                # TEMU still goes through the listings-table path (no
                # direct API adapter implemented yet).
                summary["skipped"] += 1
                await _append_detail(
                    session,
                    job,
                    {
                        "integration_id": str(integ.id),
                        "integration_name": integ.name,
                        "platform": integ.platform.value,
                        "result": "deferred_to_listings_cron",
                        "note": "Temu auto-link uses listings_import cron",
                    },
                )
        job.result = summary
        # Job is FAILED only when *every* integration failed (no successes).
        # Otherwise SUCCEEDED — the UI can flag partial failures via
        # `result.failed_integrations` and the per-integration details.
        if (
            summary["failed_integrations"] > 0
            and summary["ok_integrations"] == 0
            and summary["skipped"] == 0
        ):
            job.status = BackgroundJobStatus.FAILED
            job.error = (
                f"all {summary['failed_integrations']} integration(s) failed"
            )
        else:
            job.status = BackgroundJobStatus.SUCCEEDED
            if summary["failed_integrations"] > 0:
                job.error = (
                    f"{summary['failed_integrations']}/"
                    f"{summary['failed_integrations'] + summary['ok_integrations']}"
                    " integration(s) failed"
                )
    except Exception as e:  # noqa: BLE001
        logger.exception("auto_link_failed", job_id=str(job_id))
        job.status = BackgroundJobStatus.FAILED
        job.error = f"{type(e).__name__}: {e}"[:1000]
    finally:
        job.finished_at = _now()
        await session.commit()
