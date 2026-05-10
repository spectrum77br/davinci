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

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Iterable
from uuid import UUID

import structlog
from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

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
    ):
        self.session = session
        self.user_id = user_id
        self.job = job
        self.report = OrchestratorReport()
        self._client_cache: dict[UUID, object] = {}
        self._integration_cache: dict[UUID, Integration] = {}
        self._store_cache: dict[UUID, Store] = {}

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

        c = client_for(integration.platform, creds, on_token_refresh=_persist_refresh)
        self._client_cache[integration.id] = c
        return c

    async def _refresh_bling(self, product: Product, link: ProductLink) -> SyncResult:
        """Bling refresh-only path. Pull stock from Bling, write back to local
        product + link. No outbound stock push in 4a."""
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
            raw = await client.get_product(bling_product_id)
        except Exception as e:  # noqa: BLE001
            return SyncResult(
                status=SyncStatus.RETRYABLE,
                error_code="bling_get_product_failed",
                error_detail=str(e)[:500],
            )
        parsed = parse_bling_product(raw) if raw else {}
        new_stock = parsed.get("stock")
        qty_before = link.stock
        if new_stock is None:
            return SyncResult(
                status=SyncStatus.SKIPPED,
                qty_before=qty_before,
                error_code="bling_stock_missing",
            )
        link.stock = int(new_stock)
        product.stock = int(new_stock)
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

    async def _process_link(self, product: Product, link: ProductLink) -> None:
        store = await self._get_store(link.store_id)
        bling_store_id = store.bling_store_id if store is not None else None  # type: ignore[attr-defined]

        async with time_sync(link.platform.value) as bucket:
            if link.platform == IntegrationPlatform.BLING:
                action = SyncLogAction.REFRESH_BLING
                result = await self._refresh_bling(product, link)
            else:
                action = SyncLogAction.UPDATE_STOCK
                try:
                    integration = await self._get_integration(link.integration_id)
                    client = await self._client(integration)
                    qty = product.stock
                    result = await client.update_stock(  # type: ignore[union-attr]
                        link, qty, bling_store_id=bling_store_id
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

        await self._emit_link_alerts(product, link, result)

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
            for link in links:
                await self._process_link(product, link)
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
