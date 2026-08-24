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
from sqlalchemy import and_, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session_scope
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
from app.services.marketplaces.magalu import MagaluClient
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

# Fan-out das integrações: em vez de processar as ~17 contas em série (uma conta
# lenta segurava as outras 16), roda até AUTOLINK_CONCURRENCY em paralelo, cada
# uma na sua própria AsyncSession. Cabe folgado no pool de 30 do processo
# (1 sessão principal + AUTOLINK_CONCURRENCY sub-sessões).
AUTOLINK_CONCURRENCY = 4

# "Nunca abandona a conta": cada integração é retentada até AUTOLINK_MAX_TRIES
# vezes dentro do run, com backoff entre as tentativas. Esgotou e ainda falha →
# marcada PENDENTE (visível no summary/log do job), sem derrubar as outras.
AUTOLINK_MAX_TRIES = 3
# Espera (s) ANTES da 2ª e 3ª tentativas. Módulo-level p/ os testes zerarem.
AUTOLINK_BACKOFF_SECONDS = [2.0, 5.0]

# Plataformas com adapter direto de auto-link. As demais (ex.: TEMU) caem no
# cron de listings e são contadas como `skipped`.
_ADAPTED_PLATFORMS = {
    IntegrationPlatform.BLING,
    IntegrationPlatform.TIKTOK,
    IntegrationPlatform.ML,
    IntegrationPlatform.SHOPEE,
    IntegrationPlatform.AMAZON,
    IntegrationPlatform.MAGALU,
}


def _now() -> datetime:
    return datetime.now(UTC)


def _loop_now() -> float:
    return time.monotonic()


async def _heartbeat(session: AsyncSession, job_id: UUID) -> None:
    """Liveness only: bumps `last_heartbeat_at` via atomic SQL so
    background_jobs_gc não marca o job como órfão durante um scan longo. Não
    toca em `processed` (contabilizado por integração em `_bump_processed`),
    então é seguro chamar de sub-sessões concorrentes."""
    await session.execute(
        update(BackgroundJob)
        .where(BackgroundJob.id == job_id)
        .values(last_heartbeat_at=_now())
    )
    await session.commit()


async def _bump_processed(session: AsyncSession, job_id: UUID, n: int) -> None:
    """Incremento RELATIVO e atômico de `processed` (+ heartbeat). Relativo
    porque N integrações commitam em paralelo em sessões distintas — um
    `SET processed = :abs` perderia updates (padrão do SyncOrchestrator)."""
    if n <= 0:
        return
    await session.execute(
        update(BackgroundJob)
        .where(BackgroundJob.id == job_id)
        .values(
            processed=func.coalesce(BackgroundJob.processed, 0) + n,
            last_heartbeat_at=_now(),
        )
    )
    await session.commit()


async def _append_detail(
    session: AsyncSession, job_id: UUID, entry: dict[str, Any]
) -> None:
    # Off the hot job row: one INSERT into background_job_details instead of
    # rewriting the JSONB array. Called once per integration (low volume), so
    # the commit here is cheap and keeps the per-integration progress visible.
    await append_job_detail(session, job_id, entry)
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
    job_id: UUID,
    integ: Integration,
) -> dict[str, Any]:
    """Returns o dict de stats consumido por `_run_one`."""
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
        if created % 25 == 0:
            await _heartbeat(session, job_id)
    await session.commit()
    return _stats(created=created, already=already)


async def _link_tiktok_integration(
    session: AsyncSession,
    job_id: UUID,
    integ: Integration,
) -> dict[str, Any]:
    """Returns o dict de stats consumido por `_run_one`.

    Iterates `client.search_products` in pages of 100, matching each TikTok
    SKU's `seller_sku` against the user's `products.sku` (case-insensitive).
    For every match without an existing `product_links` row, creates one
    with `platform=tiktok` and the TikTok product/sku ids in
    `external_id` / `variation_id`.
    """
    products = (await session.execute(select(Product))).scalars().all()
    sku_index = _SkuIndex(products)

    # Key on (external_id, variation_id) to match the DB unique index
    # (product_id is NOT part of it). Keying on product_id — as before — meant a
    # seller_sku edited to another product produced a *new* key, so auto-link
    # tried to INSERT a duplicate identity and the whole commit 23505'd; now it
    # re-points instead.
    existing_by_key: dict[tuple[str, str], ProductLink] = {}
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
        existing_by_key[(link.external_id or "", link.variation_id or "")] = link

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
    repointed = 0
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
                key = (product_id, sku_id)
                existing_link = existing_by_key.get(key)
                if existing_link is not None:
                    if existing_link.product_id == local.id:
                        already += 1
                        continue
                    # seller_sku editado no anúncio TikTok → re-aponta o link
                    # pro produto que o SKU passou a resolver (o sku_id interno
                    # é estável na edição do seller_sku).
                    _repoint_link(
                        existing_link,
                        product_id=local.id,
                        external_sku=seller_sku,
                        title=title,
                        stock=sku.get("stock"),
                    )
                    repointed += 1
                    continue
                new_link = ProductLink(
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
                session.add(new_link)
                existing_by_key[key] = new_link
                created += 1
                if created % 25 == 0:
                    await _heartbeat(session, job_id)
        if not page_token:
            break

    await session.commit()
    return _stats(
        created=created, already=already, not_found=not_found,
        sku_vazio=sku_vazio, repointed=repointed, sku_index=sku_index, error=error,
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


def _repoint_link(
    link: ProductLink,
    *,
    product_id: Any,
    external_sku: str,
    title: str | None,
    stock: int | None,
) -> None:
    """Move an existing link onto `product_id` because the marketplace's
    current SELLER_SKU now belongs to a different product. The identity tuple
    (user, platform, integration, external_id, variation_id) is unchanged, so
    the re-point never collides with the unique index. Callers set
    `listing_type` themselves when the platform carries one."""
    link.product_id = product_id
    link.external_sku = external_sku
    link.listing_title = title
    link.stock = stock
    link.last_sync_status = LinkSyncStatus.OK
    link.last_sync_at = _now()


async def _link_via_listings(
    session: AsyncSession,
    job_id: UUID,
    integ: Integration,
    client,
    platform: IntegrationPlatform,
    *,
    repoint: bool = False,
) -> dict[str, Any]:
    """Generic adapter: walk `client.list_listings()` (which already yields
    normalized {external_id, sku, title, listing_type, …} dicts for ML and
    Shopee) and create `product_links` for every listing whose SKU matches
    a row in `products`. Returns o dict de stats consumido por `_run_one`.

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
        await _heartbeat(session, job_id)

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
                _repoint_link(
                    existing_link,
                    product_id=local.id,
                    external_sku=sku,
                    title=listing.get("title"),
                    stock=listing.get("stock"),
                )
                existing_link.listing_type = listing.get("listing_type")
                repointed += 1
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


def _tiktok_client_for(integ: Integration, session: AsyncSession) -> TikTokClient:
    creds = decrypt_json(integ.credentials) if integ.credentials else {}

    async def _persist_refresh(new_creds: dict) -> None:
        integ.credentials = encrypt_json(new_creds)
        await session.commit()

    return TikTokClient(creds, on_token_refresh=_persist_refresh)


def _amazon_client_for(integ: Integration, session: AsyncSession) -> AmazonClient:
    creds = decrypt_json(integ.credentials) if integ.credentials else {}

    async def _persist_refresh(new_creds: dict) -> None:
        integ.credentials = encrypt_json(new_creds)
        await session.commit()

    return AmazonClient(creds, on_token_refresh=_persist_refresh)


def _magalu_client_for(integ: Integration, session: AsyncSession) -> MagaluClient:
    creds = decrypt_json(integ.credentials) if integ.credentials else {}

    async def _persist_refresh(new_creds: dict) -> None:
        integ.credentials = encrypt_json(new_creds)
        exp = new_creds.get("expires_at")
        if exp:
            integ.token_expires_at = datetime.fromtimestamp(int(exp), tz=UTC)
        await session.commit()

    return MagaluClient(creds, on_token_refresh=_persist_refresh)


async def _link_amazon_integration(
    session: AsyncSession,
    job_id: UUID,
    integ: Integration,
) -> dict[str, Any]:
    """Amazon-specific adapter: pulls the GET_MERCHANT_LISTINGS_ALL_DATA
    report (TSV via Reports API), matches by seller-SKU, and bulk-inserts
    product_links in chunks of 100 (mirroring SSH's createProductLinksBulk).

    Dedup key INCLUDES `integration_id` because the same SKU can exist in
    multiple Amazon seller accounts (e.g., MFN + FBA, or two regions).

    Returns o dict de stats consumido por `_run_one`.
    """
    client = _amazon_client_for(integ, session)

    products = (await session.execute(select(Product))).scalars().all()
    sku_index = _SkuIndex(products)

    # Same rule as _link_via_listings: dedup on (external_id, variation_id)
    # since the DB unique constraint excludes product_id. existing_by_key is
    # already scoped to this integration via the WHERE clause below.
    existing_by_key: dict[tuple[str, str], ProductLink] = {}
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
        existing_by_key[(link.external_id or "", link.variation_id or "")] = link

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
        await _heartbeat(session, job_id)

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
                if existing_link.product_id == local.id:
                    already += 1
                    continue
                # Raramente dispara na Amazon: o external_id É o seller-sku
                # (chave imutável do anúncio), então editar o SKU cria um
                # anúncio novo em vez de re-mapear este. Mantido por
                # uniformidade com ML/Shopee/TikTok.
                _repoint_link(
                    existing_link,
                    product_id=local.id,
                    external_sku=listing.get("external_sku") or sku,
                    title=listing.get("title"),
                    stock=listing.get("stock"),
                )
                existing_link.listing_type = listing.get("listing_type")
                repointed += 1
                continue
            new_link = ProductLink(
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
            pending.append(new_link)
            existing_by_key[key] = new_link
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
        sku_vazio=sku_vazio, repointed=repointed, sku_index=sku_index, error=error,
    )


async def _link_dispatch(
    session: AsyncSession, job_id: UUID, integ: Integration
) -> dict[str, Any]:
    """Roteia p/ o adapter da plataforma. Só é chamado p/ plataformas em
    `_ADAPTED_PLATFORMS`. Retorna o dict de stats (com `error` setado em falha
    tratada); pode levantar em falha não-tratada — o `_link_with_retry` captura."""
    platform = integ.platform
    if platform == IntegrationPlatform.BLING:
        return await _link_bling_integration(session, job_id, integ)
    if platform == IntegrationPlatform.TIKTOK:
        return await _link_tiktok_integration(session, job_id, integ)
    if platform == IntegrationPlatform.ML:
        return await _link_via_listings(
            session, job_id, integ, _ml_client_for(integ, session),
            IntegrationPlatform.ML, repoint=True,
        )
    if platform == IntegrationPlatform.SHOPEE:
        return await _link_via_listings(
            session, job_id, integ, _shopee_client_for(integ, session),
            IntegrationPlatform.SHOPEE, repoint=True,
        )
    if platform == IntegrationPlatform.MAGALU:
        # Magalu's listing SKU (com hífen) e o external_id andam juntos — o
        # `list_listings` já devolve external_id=SKU-com-hífen e sku=SKU-com-ponto
        # (canônico do Bling/produtos), então o match usa o ponto e o link
        # guarda o hífen p/ o PATCH de estoque. repoint=True por uniformidade
        # (na prática nunca dispara: mudar o SKU na Magalu troca o external_id).
        return await _link_via_listings(
            session, job_id, integ, _magalu_client_for(integ, session),
            IntegrationPlatform.MAGALU, repoint=True,
        )
    if platform == IntegrationPlatform.AMAZON:
        return await _link_amazon_integration(session, job_id, integ)
    return _stats(error=f"no_adapter:{platform.value}")


async def _link_with_retry(
    session: AsyncSession, job_id: UUID, integ: Integration
) -> tuple[dict[str, Any], bool]:
    """Roda o adapter da plataforma, retentando até `AUTOLINK_MAX_TRIES` vezes
    em caso de falha (exceção OU `stats['error']`), com backoff entre as
    tentativas. Os adapters são idempotentes (already_present/repoint), então
    re-rodar é seguro. Retorna `(stats, pending)` — `pending=True` quando esgotou
    as tentativas e a conta ainda falha: ela é marcada PENDENTE em vez de sumir.
    """
    stats: dict[str, Any] = {}
    for attempt in range(1, AUTOLINK_MAX_TRIES + 1):
        try:
            stats = await _link_dispatch(session, job_id, integ)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "auto_link_integration_attempt_failed",
                integration_id=str(integ.id),
                platform=integ.platform.value,
                attempt=attempt,
                err=str(e)[:300],
            )
            stats = _stats(error=f"{type(e).__name__}: {str(e)[:300]}")
            # A exceção pode ter abortado a transação — limpa antes de seguir,
            # senão o _bump_processed/_append_detail seguintes falhariam.
            try:
                await session.rollback()
            except Exception:  # noqa: BLE001
                logger.debug(
                    "auto_link_retry_rollback_failed", integration_id=str(integ.id)
                )
        if not stats.get("error"):
            return stats, False
        if attempt < AUTOLINK_MAX_TRIES:
            idx = attempt - 1
            delay = (
                AUTOLINK_BACKOFF_SECONDS[idx]
                if idx < len(AUTOLINK_BACKOFF_SECONDS)
                else AUTOLINK_BACKOFF_SECONDS[-1]
            )
            if delay > 0:
                await asyncio.sleep(delay)
    return stats, True


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

    job.total = len(integrations) or 1
    await session.commit()

    # Snapshot só os ids — cada task re-busca sua Integration na SUA sessão
    # (sessions SQLAlchemy não são compartilháveis entre tasks concorrentes).
    integ_ids = [i.id for i in integrations]

    summary = {
        "created": 0,
        "already_present": 0,
        "skipped": 0,
        "not_found": 0,
        "sku_vazio": 0,
        "sku_ambiguo": 0,
        "repointed": 0,
        # `failed_integrations` mantido (o front lê essa chave) e agora é
        # sinônimo de `pending_integrations`: contas que esgotaram os retries.
        "failed_integrations": 0,
        "pending_integrations": 0,
        "ok_integrations": 0,
    }
    sem = asyncio.Semaphore(AUTOLINK_CONCURRENCY)

    def _accumulate(stats: dict[str, Any]) -> None:
        # Bloco 100% síncrono (sem await): seguro entre tasks cooperativas do
        # asyncio — nenhuma outra corrotina roda no meio de um += sem await.
        summary["created"] += stats.get("created", 0)
        summary["already_present"] += stats.get("already_present", 0)
        summary["not_found"] += stats.get("not_found", 0)
        summary["sku_vazio"] += stats.get("sku_vazio", 0)
        summary["sku_ambiguo"] += stats.get("sku_ambiguo", 0)
        summary["repointed"] += stats.get("repointed", 0)

    async def _run_one(integ_id: UUID) -> None:
        async with sem:
            async with session_scope() as sub_s:
                integ = await sub_s.get(Integration, integ_id)
                if integ is None:
                    return
                if integ.platform not in _ADAPTED_PLATFORMS:
                    # TEMU etc: sem adapter direto → cai no cron de listings.
                    summary["skipped"] += 1
                    await _append_detail(
                        sub_s,
                        job_id,
                        {
                            "integration_id": str(integ.id),
                            "integration_name": integ.name,
                            "platform": integ.platform.value,
                            "result": "deferred_to_listings_cron",
                            "note": "Temu auto-link uses listings_import cron",
                        },
                    )
                    return
                stats, pending = await _link_with_retry(sub_s, job_id, integ)
                await _bump_processed(
                    sub_s,
                    job_id,
                    stats.get("created", 0) + stats.get("repointed", 0),
                )
                if pending:
                    summary["pending_integrations"] += 1
                    summary["failed_integrations"] += 1
                    result = "pending"
                else:
                    summary["ok_integrations"] += 1
                    result = "ok"
                _accumulate(stats)
                await _append_detail(
                    sub_s,
                    job_id,
                    {
                        "integration_id": str(integ.id),
                        "integration_name": integ.name,
                        "platform": integ.platform.value,
                        "result": result,
                        "pending": pending,
                        **stats,
                    },
                )

    try:
        # Fan-out: uma conta lenta/instável não segura as outras. Cada task tem
        # seu retry+backoff; uma que estoure de vez fica PENDENTE, nunca some.
        await asyncio.gather(*[_run_one(i) for i in integ_ids])
        job.result = summary
        # FAILED só quando NENHUMA integração concluiu (nem ok, nem skipped) e há
        # pendentes. Senão SUCCEEDED — o parcial fica visível em
        # result.pending_integrations + nos detalhes por integração.
        if (
            summary["pending_integrations"] > 0
            and summary["ok_integrations"] == 0
            and summary["skipped"] == 0
        ):
            job.status = BackgroundJobStatus.FAILED
            job.error = (
                f"all {summary['pending_integrations']} integration(s) pending"
            )
        else:
            job.status = BackgroundJobStatus.SUCCEEDED
            if summary["pending_integrations"] > 0:
                total_adapted = (
                    summary["pending_integrations"] + summary["ok_integrations"]
                )
                job.error = (
                    f"{summary['pending_integrations']}/{total_adapted}"
                    " integration(s) pending"
                )
    except Exception as e:  # noqa: BLE001
        logger.exception("auto_link_failed", job_id=str(job_id))
        job.status = BackgroundJobStatus.FAILED
        job.error = f"{type(e).__name__}: {e}"[:1000]
    finally:
        job.finished_at = _now()
        await session.commit()
