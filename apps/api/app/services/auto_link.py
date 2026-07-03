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

import asyncio
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
from app.services.job_details import append_job_detail
from app.services.marketplaces.amazon import AmazonClient
from app.services.marketplaces.ml import MercadoLivreClient
from app.services.marketplaces.shopee import ShopeeClient
from app.services.marketplaces.tiktok import TikTokClient

logger = structlog.get_logger()

# Hard wall-clock cap on a single TikTok search page. The TikTokClient already
# carries an httpx connect+read timeout, but httpx's read timeout resets on each
# byte received, so a trickling/wedged response can outlive it. asyncio.wait_for
# is a true elapsed-time bound: if one page doesn't come back in time we log,
# stop this account, and let the other integrations keep linking (the old code
# only checked the 2-min TIMEOUT_SECONDS *between* pages, so a single hung
# search_products call blocked TikTok linking indefinitely). Module-level so
# tests can shrink it.
TIKTOK_PAGE_TIMEOUT_SECONDS = 45.0


def _now() -> datetime:
    return datetime.now(UTC)


def _loop_now() -> float:
    return time.monotonic()


async def _heartbeat(session: AsyncSession, job: BackgroundJob) -> None:
    job.last_heartbeat_at = _now()
    await session.commit()


async def _append_detail(session: AsyncSession, job: BackgroundJob, entry: dict[str, Any]) -> None:
    # Off the hot job row: one INSERT into background_job_details instead of
    # rewriting the JSONB array. Called once per integration (low volume), so
    # the commit here is cheap and keeps the per-integration progress visible.
    await append_job_detail(session, job.id, entry)
    await session.commit()


def _norm_sku(raw: str | None) -> str:
    """Canonical SKU for matching: trim + lower + colapsa espaços internos.
    Usada nos DOIS lados do match (products.sku e seller_sku do marketplace);
    sufixos/acentos ficam intactos de propósito — remoção silenciosa criaria
    matches falsos."""
    return " ".join((raw or "").split()).lower()


class _SkuIndex:
    """products.sku → Product, com colisão explícita em vez de sobrescrita.

    O dict[str, Product] antigo deixava o ÚLTIMO produto do loop vencer quando
    dois produtos compartilham SKU (cadastro duplicado no Bling) — o listing
    linkava num dos gêmeos ao acaso e o outro ficava 'sem anúncio' na tela.
    Aqui SKU ambíguo não linka: conta em `sku_ambiguo` e aparece no detail.
    """

    def __init__(self, products: list[Product]):
        self._by_sku: dict[str, list[Product]] = {}
        for p in products:
            key = _norm_sku(p.sku)
            if key:
                self._by_sku.setdefault(key, []).append(p)
        self.ambiguous_hits: dict[str, int] = {}

    def resolve(self, sku: str | None) -> tuple[Product | None, str]:
        """→ (product, motivo) com motivo ∈ {'match', 'not_found', 'ambiguo'}."""
        key = _norm_sku(sku)
        candidates = self._by_sku.get(key)
        if not candidates:
            return None, "not_found"
        if len(candidates) > 1:
            self.ambiguous_hits[key] = self.ambiguous_hits.get(key, 0) + 1
            return None, "ambiguo"
        return candidates[0], "match"

    @property
    def ambiguous_count(self) -> int:
        return sum(self.ambiguous_hits.values())

    def ambiguous_sample(self, limit: int = 5) -> list[str]:
        return sorted(self.ambiguous_hits)[:limit]


def _stats(
    *,
    created: int = 0,
    already: int = 0,
    not_found: int = 0,
    sku_vazio: int = 0,
    repointed: int = 0,
    sku_index: _SkuIndex | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "created": created,
        "already_present": already,
        "not_found": not_found,
        "sku_vazio": sku_vazio,
        "sku_ambiguo": 0,
        "repointed": repointed,
        "error": error,
    }
    if sku_index is not None and sku_index.ambiguous_count:
        out["sku_ambiguo"] = sku_index.ambiguous_count
        out["sku_ambiguo_sample"] = sku_index.ambiguous_sample()
    return out


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
) -> dict[str, Any]:
    """Returns o dict de stats consumido por `_record`.

    Iterates `client.search_products` in pages of 100, matching each TikTok
    SKU's `seller_sku` against the user's `products.sku` (case-insensitive).
    For every match without an existing `product_links` row, creates one
    with `platform=tiktok` and the TikTok product/sku ids in
    `external_id` / `variation_id`.
    """
    products = (await session.execute(select(Product))).scalars().all()
    sku_index = _SkuIndex(products)

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
    sku_vazio = 0
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
            tk_products, page_token = await asyncio.wait_for(
                client.search_products(page_size=100, page_token=page_token),
                timeout=TIKTOK_PAGE_TIMEOUT_SECONDS,
            )
        except TimeoutError:  # asyncio.wait_for raises TimeoutError on timeout
            logger.warning(
                "auto_link_tiktok_page_timeout",
                integration_id=str(integ.id),
                page=page_idx,
                timeout=TIKTOK_PAGE_TIMEOUT_SECONDS,
            )
            error = f"search_timeout: page {page_idx} exceeded {TIKTOK_PAGE_TIMEOUT_SECONDS:.0f}s"
            break
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
                if not sku_id:
                    continue
                if not seller_sku:
                    sku_vazio += 1
                    continue
                local, motivo = sku_index.resolve(seller_sku)
                if local is None:
                    if motivo == "not_found":
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
    return _stats(
        created=created, already=already, not_found=not_found,
        sku_vazio=sku_vazio, sku_index=sku_index, error=error,
    )


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
    *,
    repoint: bool = False,
) -> dict[str, Any]:
    """Generic adapter: walk `client.list_listings()` (which already yields
    normalized {external_id, sku, title, listing_type, …} dicts for ML and
    Shopee) and create `product_links` for every listing whose SKU matches
    a row in `products`. Returns o dict de stats consumido por `_record`.

    `repoint=True` (ML only) re-points an existing link when the marketplace
    changed the listing's SELLER_SKU so it now resolves to a *different*
    DaVinci product. Without it the old behavior treated the row as
    "already_present" and skipped it — leaving the anúncio frozen on the
    product that USED to carry that SKU and its stock stranded (the z0215
    catalog-anúncio bug: 5 MLBs pinned to dg025.ra after the seller edited the
    SKU on ML). Left off for Shopee to keep that path's behavior unchanged.
    """
    products = (await session.execute(select(Product))).scalars().all()
    sku_index = _SkuIndex(products)

    # Dedup key matches the DB unique constraint (uq_product_links_identity):
    # (user_id, platform, integration_id, external_id, COALESCE(variation_id, '')).
    # We already filter existing_keys by integration_id below, so we just key
    # the in-memory set on (external_id, variation_id). Including product_id
    # would allow two DaVinci products that share a SKU to both queue a link
    # for the same marketplace listing — and the DB then 23505s out.
    existing_keys: set[tuple[str, str]] = set()
    # Same key → the actual ProductLink row, so we can re-point it in place when
    # the marketplace SKU moved it to another product (repoint path).
    existing_by_key: dict[tuple[str, str], ProductLink] = {}
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
        ext = link.external_id or ""
        var = link.variation_id or ""
        existing_keys.add((ext, var))
        existing_by_key[(ext, var)] = link
        # Encoding legado da Shopee (promoção via listings): o model_id vinha
        # embutido no external_id ("item_model") com variation_id vazio. Sem
        # decompor aqui, o mesmo anúncio ganharia um segundo link no formato
        # canônico (item, model) — era a causa do estoque 2x na tela.
        if platform == IntegrationPlatform.SHOPEE and not var and "_" in ext:
            base, _, model = ext.partition("_")
            if base.isdigit() and model.isdigit():
                existing_keys.add((base, model))

    pending: list[ProductLink] = []
    created = 0
    skipped = 0
    already = 0
    not_found = 0
    sku_vazio = 0
    repointed = 0
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
                sku_vazio += 1
                continue
            local, motivo = sku_index.resolve(sku)
            if local is None:
                if motivo == "not_found":
                    not_found += 1
                continue
            key = (external_id, variation_id or "")
            existing_link = existing_by_key.get(key)
            if existing_link is not None:
                if not repoint or existing_link.product_id == local.id:
                    already += 1
                    continue
                # Marketplace SKU changed → re-point the stored row to the
                # product it now belongs to (see docstring).
                existing_link.product_id = local.id
                existing_link.external_sku = sku
                existing_link.listing_title = listing.get("title")
                existing_link.listing_type = listing.get("listing_type")
                existing_link.stock = listing.get("stock")
                existing_link.last_sync_status = LinkSyncStatus.OK
                existing_link.last_sync_at = _now()
                repointed += 1
                job.processed = (job.processed or 0) + 1
                continue
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
    return _stats(
        created=created, already=already, not_found=not_found,
        sku_vazio=sku_vazio, repointed=repointed, sku_index=sku_index, error=error,
    )


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
) -> dict[str, Any]:
    """Amazon-specific adapter: pulls the GET_MERCHANT_LISTINGS_ALL_DATA
    report (TSV via Reports API), matches by seller-SKU, and bulk-inserts
    product_links in chunks of 100 (mirroring SSH's createProductLinksBulk).

    Dedup key INCLUDES `integration_id` because the same SKU can exist in
    multiple Amazon seller accounts (e.g., MFN + FBA, or two regions).

    Returns o dict de stats consumido por `_record`.
    """
    client = _amazon_client_for(integ, session)

    products = (await session.execute(select(Product))).scalars().all()
    sku_index = _SkuIndex(products)

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
    sku_vazio = 0
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
                sku_vazio += 1
                continue
            local, motivo = sku_index.resolve(sku)
            if local is None:
                if motivo == "not_found":
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
    return _stats(
        created=created, already=already, not_found=not_found,
        sku_vazio=sku_vazio, sku_index=sku_index, error=error,
    )


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
        "sku_vazio": 0,
        "sku_ambiguo": 0,
        "repointed": 0,
        "failed_integrations": 0,
        "ok_integrations": 0,
    }

    async def _record(integ: Integration, stats: dict[str, Any]) -> None:
        summary["created"] += stats.get("created", 0)
        summary["already_present"] += stats.get("already_present", 0)
        summary["not_found"] += stats.get("not_found", 0)
        summary["sku_vazio"] += stats.get("sku_vazio", 0)
        summary["sku_ambiguo"] += stats.get("sku_ambiguo", 0)
        summary["repointed"] += stats.get("repointed", 0)
        error = stats.get("error")
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
                **stats,
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
                    integ, _stats(created=created, already=already, error=error)
                )
            elif integ.platform == IntegrationPlatform.TIKTOK:
                await _record(
                    integ, await _link_tiktok_integration(session, job, integ)
                )
            elif integ.platform == IntegrationPlatform.ML:
                client = _ml_client_for(integ, session)
                await _record(
                    integ,
                    await _link_via_listings(
                        session, job, integ, client, IntegrationPlatform.ML,
                        repoint=True,
                    ),
                )
            elif integ.platform == IntegrationPlatform.SHOPEE:
                client = _shopee_client_for(integ, session)
                await _record(
                    integ,
                    await _link_via_listings(
                        session, job, integ, client, IntegrationPlatform.SHOPEE
                    ),
                )
            elif integ.platform == IntegrationPlatform.AMAZON:
                await _record(
                    integ, await _link_amazon_integration(session, job, integ)
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
