"""SyncOrchestrator skeleton — Fase 4a.

Pipeline:
    1. Acquire `pg_advisory_lock` for `user_id` (409 if busy).
    2. Iterate target products.
    3. For each product: load active `product_links`, dispatch by platform.
       - Bling: refresh-only (pulls source-of-truth stock from Bling, writes
         `products.stock` + `product_links.stock` — no outbound push yet).
       - ML/Shopee/Amazon/TikTok/Temu/Aliexpress: not implemented in 4a.
         Mark as `skipped` with `error_code='platform_not_implemented'`.
    4. Persist a `SyncLog` row per link processed.
    5. Heartbeat into `background_jobs` every N links.

Sub-phases 4b.* implement the real `MarketplaceClient` for each platform
without altering this orchestrator. The dispatch table reads `client_for(...)`
from `services.marketplaces.factory`, which raises `HTTPException(501)` for
unimplemented platforms — orchestrator catches that and writes `skipped`.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Iterable
from uuid import UUID

import structlog
from fastapi import HTTPException
from sqlalchemy import and_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session_scope

from app.models import (
    AlertSeverity,
    AlertType,
    BackgroundJob,
    BackgroundJobStatus,
    Integration,
    IntegrationPlatform,
    LinkSyncStatus,
    Product,
    ProductLink,
    Store,
    SyncLog,
    SyncLogAction,
)
from app.security.cipher import decrypt_json, encrypt_json
from app.services.alerts import emit_alert
from app.services.marketplaces.base import SyncResult, SyncStatus
from app.services.marketplaces.bling import BlingClient, parse_bling_product
from app.services.marketplaces.factory import client_for
from app.services.metrics import time_sync

logger = structlog.get_logger()

HEARTBEAT_EVERY = 25
DETAILS_MAX = 500
SYNC_ALL_CONCURRENCY = 8
# Bling is the source of truth — always fetch fresh stock before pushing to
# marketplaces, regardless of how recently we synced. The previous 24h cache
# kept sync_all cheap but caused stale stock on the table; the cron now pays
# the Bling rate-limit cost (handled by Bling client retry/backoff).
BLING_REFRESH_TTL_SECONDS = 0
# SSH delta #1: after the initial pass, re-sync products whose links came back
# RETRYABLE so transient marketplace blips (timeouts, 5xx) get a second chance
# without waiting for the next daily run. 30s pause before the first retry, 15s
# before each subsequent one — gives token refreshers and rate limits time to
# settle without dragging sync_all out.
MAX_RESYNC_ROUNDS = 3
RESYNC_WAIT_FIRST_SECONDS = 30
RESYNC_WAIT_SUBSEQUENT_SECONDS = 15


@dataclass(slots=True)
class OrchestratorReport:
    total_links: int = 0
    ok: int = 0
    skipped: int = 0
    retryable: int = 0
    fatal: int = 0
    requires_review: int = 0


def _status_to_link_status(s: SyncStatus) -> LinkSyncStatus:
    return {
        SyncStatus.OK: LinkSyncStatus.OK,
        SyncStatus.SKIPPED: LinkSyncStatus.SKIPPED,
        SyncStatus.RETRYABLE: LinkSyncStatus.RETRYABLE,
        SyncStatus.FATAL: LinkSyncStatus.FATAL,
        SyncStatus.REQUIRES_REVIEW: LinkSyncStatus.REQUIRES_REVIEW,
    }[s]


class SyncOrchestrator:
    def __init__(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        job: BackgroundJob | None = None,
        force_bling_refresh: bool = False,
        force: bool = False,
    ):
        self.session = session
        self.user_id = user_id
        self.job = job
        self.force_bling_refresh = force_bling_refresh
        # `force=True` bypasses marketplace-side safety guards (notably ML's
        # B1 zero-block). Used by the manual sync endpoint where the user
        # explicitly wants the marketplace to mirror Bling, even when that
        # means writing 0 on a link that previously had positive stock.
        self.force = force
        self.report = OrchestratorReport()
        self._client_cache: dict[UUID, object] = {}
        self._integration_cache: dict[UUID, Integration] = {}
        self._store_cache: dict[UUID, Store] = {}
        # SSH delta #1 — populated by run_parallel after each pass so
        # run_with_retry knows which products to re-sync.
        self._last_round_retryable_ids: list[UUID] = []

    async def _get_integration(self, integration_id: UUID) -> Integration:
        if integration_id in self._integration_cache:
            return self._integration_cache[integration_id]
        i = await self.session.get(Integration, integration_id)
        if i is None:
            raise LookupError(f"integration_not_found: {integration_id}")
        self._integration_cache[integration_id] = i
        return i

    async def _get_store(self, store_id: UUID | None) -> Store | None:
        if store_id is None:
            return None
        if store_id in self._store_cache:
            return self._store_cache[store_id]
        s = await self.session.get(Store, store_id)
        if s is not None:
            self._store_cache[store_id] = s
        return s

    async def _client(self, integration: Integration):
        if integration.id in self._client_cache:
            return self._client_cache[integration.id]
        creds = decrypt_json(integration.credentials)

        async def _persist_refresh(new_creds: dict) -> None:
            integration.credentials = encrypt_json(new_creds)
            exp = new_creds.get("expires_at")
            if exp:
                integration.token_expires_at = datetime.fromtimestamp(int(exp), tz=UTC)
            await self.session.commit()

        c = client_for(integration.platform, creds, on_token_refresh=_persist_refresh, integration_id=integration.id)
        self._client_cache[integration.id] = c
        return c

    async def _refresh_bling(self, product: Product, link: ProductLink) -> SyncResult:
        """Bling refresh-only path. Pull stock from Bling, write back to local
        product + link. No outbound stock push in 4a."""
        if not self.force_bling_refresh and link.last_sync_at is not None:
            age = (datetime.now(UTC) - link.last_sync_at).total_seconds()
            if age < BLING_REFRESH_TTL_SECONDS:
                return SyncResult(
                    status=SyncStatus.SKIPPED,
                    qty_before=link.stock,
                    qty_after=link.stock,
                    error_code="bling_refresh_cached",
                    error_detail=f"age={int(age)}s ttl={BLING_REFRESH_TTL_SECONDS}s",
                    payload={"source": "bling_refresh_cached"},
                )
        integration = await self._get_integration(link.integration_id)
        client = await self._client(integration)
        if not isinstance(client, BlingClient):
            return SyncResult(
                status=SyncStatus.SKIPPED,
                error_code="not_bling_client",
            )
        try:
            bling_product_id = int(link.external_id)
        except (TypeError, ValueError):
            return SyncResult(
                status=SyncStatus.FATAL,
                error_code="invalid_external_id",
                error_detail=f"external_id={link.external_id!r}",
            )
        try:
            smart = await client.get_product_stock_smart(
                bling_product_id, sku=product.sku
            )
            raw = smart.get("raw") or {}
            # If product was found via SKU search (deleted/inactive in Bling),
            # update the link's external_id to point to the active product.
            if smart.get("found_via") in ("sku_search", "sku_search_inactive"):
                new_bling_id = smart.get("bling_product_id")
                if new_bling_id:
                    link.external_id = str(new_bling_id)
                    product.bling_product_id = int(new_bling_id)
        except Exception as e:  # noqa: BLE001
            return SyncResult(
                status=SyncStatus.RETRYABLE,
                error_code="bling_get_product_failed",
                error_detail=str(e)[:500],
            )
        parsed = parse_bling_product(raw) if raw else {}
        new_stock = smart.get("stock") if smart.get("stock") is not None else parsed.get("stock")
        qty_before = link.stock
        if new_stock is None:
            return SyncResult(
                status=SyncStatus.SKIPPED,
                qty_before=qty_before,
                error_code="bling_stock_missing",
            )
        # SSH parity: clamp Bling stock to >= 0. Bling occasionally reports
        # negative balances (oversells, manual adjustments) but marketplaces
        # reject negative quantities, so a negative bling stock counts as 0
        # for both the local cache and the outbound push.
        clamped = max(0, int(new_stock))
        link.stock = clamped
        product.stock = clamped
        if parsed.get("min_stock") is not None:
            product.min_stock = int(parsed["min_stock"])
        if parsed.get("category"):
            product.category = parsed["category"]
        if parsed.get("observation"):
            product.observation = parsed["observation"]
        if parsed.get("bling_cost_price") is not None:
            product.bling_cost_price = parsed["bling_cost_price"]
        if parsed.get("price") is not None:
            product.price = parsed["price"]
        if parsed.get("name"):
            product.name = parsed["name"]
        if parsed.get("image_url"):
            product.image_url = parsed["image_url"]
        return SyncResult(
            status=SyncStatus.OK,
            qty_before=qty_before,
            qty_after=int(new_stock),
            payload={"source": "bling_refresh"},
        )

    async def _process_link(
        self,
        product: Product,
        link: ProductLink,
        *,
        skip_non_bling: bool = False,
    ) -> SyncResult:
        store = await self._get_store(link.store_id)
        bling_store_id = store.bling_store_id if store is not None else None  # type: ignore[attr-defined]

        async with time_sync(link.platform.value) as bucket:
            if link.platform == IntegrationPlatform.BLING:
                action = SyncLogAction.REFRESH_BLING
                result = await self._refresh_bling(product, link)
            elif skip_non_bling:
                action = SyncLogAction.UPDATE_STOCK
                result = SyncResult(
                    status=SyncStatus.SKIPPED,
                    error_code="bling_refresh_failed_no_push",
                    error_detail="local stock stale; refusing to push to marketplace",
                )
            else:
                action = SyncLogAction.UPDATE_STOCK
                try:
                    integration = await self._get_integration(link.integration_id)
                    client = await self._client(integration)
                    # Negative product.stock can leak in from direct DB writes
                    # or older imports — guard at the push site too so the
                    # clamp is enforced even if `_refresh_bling` didn't run
                    # this pass (e.g. webhook-fed stock).
                    qty = max(0, product.stock)
                    # Always push to the marketplace — SSH parity. The earlier
                    # "verify-before-send" optimization (skip when link.stock
                    # equals qty + last_sync_status=OK) was removed because
                    # external edits to the marketplace can drift link.stock
                    # without us noticing, and operators expect "sincronizar"
                    # to actually call the API every time.
                    result = await client.update_stock(  # type: ignore[union-attr]
                        link, qty, bling_store_id=bling_store_id, force=self.force
                    )
                except HTTPException as e:
                    code = "platform_not_implemented" if e.status_code == 501 else "http_error"
                    result = SyncResult(
                        status=SyncStatus.SKIPPED,
                        error_code=code,
                        error_detail=str(e.detail)[:500],
                    )
                except Exception as e:  # noqa: BLE001
                    result = SyncResult(
                        status=SyncStatus.FATAL,
                        error_code="orchestrator_exception",
                        error_detail=str(e)[:500],
                    )

            bucket.set(result.status.value)
            if result.error_code:
                bucket.error(result.error_code)

        self._tally(result.status)
        link.last_sync_status = _status_to_link_status(result.status)
        link.last_sync_at = datetime.now(UTC)
        link.last_error = (
            f"{result.error_code}: {result.error_detail}"
            if result.error_code or result.error_detail
            else None
        )
        # Mirror the marketplace-side value into our cached `link.stock` so
        # subsequent UI reads (which page through /api/products) reflect the
        # just-pushed quantity instead of the stale pre-sync value. For
        # bling refresh this is already set inside `_refresh_bling`.
        if (
            result.status == SyncStatus.OK
            and result.qty_after is not None
            and link.platform != IntegrationPlatform.BLING
        ):
            link.stock = int(result.qty_after)

        await self._emit_link_alerts(product, link, result)

        await self._append_detail(
            {
                "product_id": str(product.id),
                "sku": product.sku,
                "platform": link.platform.value,
                "integration_id": str(link.integration_id),
                "external_id": link.external_id,
                "action": action.value,
                "status": result.status.value,
                "qty_before": result.qty_before,
                "qty_after": result.qty_after,
                "error_code": result.error_code,
                "error_detail": (result.error_detail or "")[:200] or None,
            }
        )

        self.session.add(
            SyncLog(
                user_id=self.user_id,
                job_id=self.job.id if self.job else None,
                product_id=product.id,
                product_link_id=link.id,
                integration_id=link.integration_id,
                store_id=link.store_id,
                platform=link.platform,
                action=action,
                status=link.last_sync_status,
                qty_before=result.qty_before,
                qty_after=result.qty_after,
                error_code=result.error_code,
                error_detail=result.error_detail,
                payload=result.payload,
            )
        )
        return result

    async def _emit_link_alerts(
        self, product: Product, link: ProductLink, result: SyncResult
    ) -> None:
        """B5/B3: surface banned (REQUIRES_REVIEW) and FATAL outcomes as alerts.
        Dedupe per (product, link) so re-runs collapse."""
        if result.status == SyncStatus.REQUIRES_REVIEW:
            classification = (result.payload or {}).get("shopee_classification")
            if classification == "banned" or link.platform == IntegrationPlatform.SHOPEE:
                await emit_alert(
                    self.session,
                    user_id=self.user_id,
                    type=AlertType.LISTING_BANNED,
                    severity=AlertSeverity.ERROR,
                    title=f"Anúncio banido — {link.platform.value}: {product.sku}",
                    message=(result.error_detail or result.error_code or "")[:500] or None,
                    payload={
                        "product_id": str(product.id),
                        "link_id": str(link.id),
                        "platform": link.platform.value,
                        "error_code": result.error_code,
                    },
                    dedupe_key=f"listing_banned:{link.id}",
                )
            else:
                await emit_alert(
                    self.session,
                    user_id=self.user_id,
                    type=AlertType.REQUIRES_REVIEW,
                    severity=AlertSeverity.WARNING,
                    title=f"Revisão necessária — {link.platform.value}: {product.sku}",
                    message=(result.error_detail or result.error_code or "")[:500] or None,
                    payload={
                        "product_id": str(product.id),
                        "link_id": str(link.id),
                        "platform": link.platform.value,
                        "error_code": result.error_code,
                    },
                    dedupe_key=f"requires_review:{link.id}",
                )
        elif result.status == SyncStatus.FATAL:
            await emit_alert(
                self.session,
                user_id=self.user_id,
                type=AlertType.SYNC_FAILURE,
                severity=AlertSeverity.ERROR,
                title=f"Falha sync — {link.platform.value}: {product.sku}",
                message=(result.error_detail or result.error_code or "")[:500] or None,
                payload={
                    "product_id": str(product.id),
                    "link_id": str(link.id),
                    "platform": link.platform.value,
                    "error_code": result.error_code,
                },
                dedupe_key=f"sync_failure:{link.id}",
            )

    def _tally(self, s: SyncStatus) -> None:
        self.report.total_links += 1
        if s == SyncStatus.OK:
            self.report.ok += 1
        elif s == SyncStatus.SKIPPED:
            self.report.skipped += 1
        elif s == SyncStatus.RETRYABLE:
            self.report.retryable += 1
        elif s == SyncStatus.FATAL:
            self.report.fatal += 1
        elif s == SyncStatus.REQUIRES_REVIEW:
            self.report.requires_review += 1

    async def _append_detail(self, entry: dict) -> None:
        if self.job is None:
            return
        entry = {"at": datetime.now(UTC).isoformat(), **entry}
        # Atomic JSONB append + counter bump so concurrent sub-orchestrators
        # (run_parallel) never lose detail entries to read-modify-write
        # races. Trim is intentionally not done here (asyncpg rejects the
        # CASE/subquery form with repeated placeholders) — a single sync_all
        # run produces <4k entries, ~800KB JSONB, which Postgres handles
        # fine. A periodic GC could trim historical jobs if it ever matters.
        await self.session.execute(
            text(
                """
                UPDATE davinci.background_jobs
                SET details = COALESCE(details, '[]'::jsonb) || CAST(:entry AS jsonb),
                    processed = COALESCE(processed, 0) + 1,
                    last_heartbeat_at = NOW()
                WHERE id = :jid
                """
            ),
            {"jid": str(self.job.id), "entry": json.dumps(entry)},
        )
        await self.session.commit()

    async def _heartbeat(self, processed: int) -> None:
        if self.job is None:
            return
        await self.session.execute(
            update(BackgroundJob)
            .where(BackgroundJob.id == self.job.id)
            .values(processed=processed, last_heartbeat_at=datetime.now(UTC))
        )
        await self.session.commit()

    async def run(
        self,
        products: Iterable[Product],
        *,
        only_link_ids: list[UUID] | None = None,
    ) -> OrchestratorReport:
        if self.job is not None:
            self.job.status = BackgroundJobStatus.RUNNING
            self.job.started_at = datetime.now(UTC)
            await self.session.commit()

        link_filter = set(only_link_ids) if only_link_ids else None

        processed = 0
        for product in products:
            stmt = select(ProductLink).where(ProductLink.product_id == product.id)
            if link_filter is not None:
                stmt = stmt.where(ProductLink.id.in_(link_filter))
            links = (await self.session.execute(stmt)).scalars().all()
            bling_links = [l for l in links if l.platform == IntegrationPlatform.BLING]
            other_links = [l for l in links if l.platform != IntegrationPlatform.BLING]
            bling_failed = False
            for link in bling_links:
                r = await self._process_link(product, link)
                if r.status in (SyncStatus.FATAL, SyncStatus.RETRYABLE):
                    bling_failed = True
                processed += 1
                if processed % HEARTBEAT_EVERY == 0:
                    await self._heartbeat(processed)
            for link in other_links:
                await self._process_link(product, link, skip_non_bling=bling_failed)
                processed += 1
                if processed % HEARTBEAT_EVERY == 0:
                    await self._heartbeat(processed)

        await self.session.commit()

        if self.job is not None:
            self.job.status = BackgroundJobStatus.SUCCEEDED
            self.job.processed = processed
            self.job.finished_at = datetime.now(UTC)
            self.job.result = {
                "total_links": self.report.total_links,
                "ok": self.report.ok,
                "skipped": self.report.skipped,
                "retryable": self.report.retryable,
                "fatal": self.report.fatal,
                "requires_review": self.report.requires_review,
            }
            await self.session.commit()
        logger.info(
            "sync_orchestrator_done",
            **(self.job.result if self.job else asdict(self.report)),
        )
        return self.report

    async def run_parallel(
        self,
        product_ids: list[UUID],
        *,
        only_link_ids: list[UUID] | None = None,
        concurrency: int = SYNC_ALL_CONCURRENCY,
    ) -> OrchestratorReport:
        """Parallel variant of `run()` for sync_all-style runs.

        Each product is processed inside its own AsyncSession (SQLAlchemy
        sessions are not safe to share across tasks) and the per-link
        bookkeeping (`details`, `processed`, `last_heartbeat_at`) writes to
        the job row via atomic JSONB SQL — so a semaphore-capped fan-out
        is safe.

        The job ORM object on this orchestrator is updated only for
        start/finish bookkeeping. Per-link mutations happen in the sub-
        sessions and are immediately visible to other tasks via the DB.
        """
        if self.job is not None:
            self.job.status = BackgroundJobStatus.RUNNING
            self.job.started_at = datetime.now(UTC)
            # Note: do not overwrite job.total here. The caller (e.g.
            # sync_all_run worker) pre-counts links so processed/total
            # stays a real ratio. If unset, leave it as-is.
            await self.session.commit()
        # Detach the job from the parent session so sub-sessions don't
        # contend on the same row in cached ORM state. Sub-orchestrators
        # rely on atomic SQL for job writes.
        job_id = self.job.id if self.job is not None else None
        user_id = self.user_id
        sem = asyncio.Semaphore(concurrency)
        link_filter = set(only_link_ids) if only_link_ids else None

        async def process_one(pid: UUID) -> OrchestratorReport | None:
            async with sem:
                try:
                    async with session_scope() as sub_s:
                        p = await sub_s.get(Product, pid)
                        if p is None:
                            return None
                        stmt = select(ProductLink).where(
                            ProductLink.product_id == p.id
                        )
                        if link_filter is not None:
                            stmt = stmt.where(ProductLink.id.in_(link_filter))
                        links = (await sub_s.execute(stmt)).scalars().all()

                        sub_job = (
                            await sub_s.get(BackgroundJob, job_id)
                            if job_id is not None
                            else None
                        )
                        sub_orch = SyncOrchestrator(
                            sub_s,
                            user_id=user_id,
                            job=sub_job,
                            force_bling_refresh=self.force_bling_refresh,
                            force=self.force,
                        )

                        bling_links = [
                            l for l in links
                            if l.platform == IntegrationPlatform.BLING
                        ]
                        other_links = [
                            l for l in links
                            if l.platform != IntegrationPlatform.BLING
                        ]
                        bling_failed = False
                        for link in bling_links:
                            r = await sub_orch._process_link(p, link)
                            if r.status in (
                                SyncStatus.FATAL,
                                SyncStatus.RETRYABLE,
                            ):
                                bling_failed = True
                        for link in other_links:
                            await sub_orch._process_link(
                                p, link, skip_non_bling=bling_failed
                            )
                        await sub_s.commit()
                        return sub_orch.report
                except Exception as e:  # noqa: BLE001
                    logger.exception(
                        "sync_parallel_product_failed",
                        product_id=str(pid),
                        err=str(e),
                    )
                    return None

        tasks = [process_one(pid) for pid in product_ids]
        round_start = datetime.now(UTC)
        results = await asyncio.gather(*tasks, return_exceptions=False)

        for r in results:
            if r is None:
                continue
            self.report.total_links += r.total_links
            self.report.ok += r.ok
            self.report.skipped += r.skipped
            self.report.retryable += r.retryable
            self.report.fatal += r.fatal
            self.report.requires_review += r.requires_review

        # SSH delta #1 — collect products whose links came back RETRYABLE in
        # this pass so run_with_retry can re-sync just those. Filter by
        # round_start so a prior round's noise doesn't carry over.
        retryable_rows = (
            await self.session.execute(
                select(ProductLink.product_id)
                .where(
                    and_(
                        ProductLink.product_id.in_([pid for pid in product_ids]),
                        ProductLink.last_sync_status == LinkSyncStatus.RETRYABLE,
                        ProductLink.last_sync_at >= round_start,
                    )
                )
                .distinct()
            )
        ).all()
        self._last_round_retryable_ids = [row[0] for row in retryable_rows]

        if self.job is not None:
            self.job.status = BackgroundJobStatus.SUCCEEDED
            self.job.finished_at = datetime.now(UTC)
            self.job.result = {
                "total_links": self.report.total_links,
                "ok": self.report.ok,
                "skipped": self.report.skipped,
                "retryable": self.report.retryable,
                "fatal": self.report.fatal,
                "requires_review": self.report.requires_review,
            }
            await self.session.commit()

        return self.report

    async def run_with_retry(
        self,
        product_ids: list[UUID],
        *,
        only_link_ids: list[UUID] | None = None,
        concurrency: int = SYNC_ALL_CONCURRENCY,
        max_rounds: int = MAX_RESYNC_ROUNDS,
    ) -> OrchestratorReport:
        """SSH delta #1 — run the initial sync_parallel pass, then re-sync
        products whose links came back RETRYABLE up to `max_rounds` times
        with progressive waits (30s for the first retry, 15s after that).

        Each round inherits the parent SyncOrchestrator's report and job
        state; the underlying `run_parallel` updates job.status/finished_at
        on every call so the final state reflects the last round.
        """
        await self.run_parallel(
            product_ids,
            only_link_ids=only_link_ids,
            concurrency=concurrency,
        )
        rounds_run = 0
        for round_idx in range(1, max_rounds):
            retry_ids = list(self._last_round_retryable_ids)
            if not retry_ids:
                break
            wait_s = (
                RESYNC_WAIT_FIRST_SECONDS
                if round_idx == 1
                else RESYNC_WAIT_SUBSEQUENT_SECONDS
            )
            logger.info(
                "sync_retry_round_waiting",
                round=round_idx,
                wait_seconds=wait_s,
                product_count=len(retry_ids),
            )
            await asyncio.sleep(wait_s)
            # run_parallel will reset _last_round_retryable_ids based on the
            # outcome of this round's products.
            await self.run_parallel(
                retry_ids,
                only_link_ids=only_link_ids,
                concurrency=concurrency,
            )
            rounds_run = round_idx
        logger.info(
            "sync_orchestrator_with_retry_done",
            retry_rounds=rounds_run,
            **(self.job.result if self.job else asdict(self.report)),
        )
        return self.report
