"""One-shot job: re-fetch stock for ML links that look broken (B2).

Selects `product_links` where:
    platform = 'ml' AND (last_sync_at IS NULL OR (stock = 0 AND last_sync_status != 'ok'))

Calls `MercadoLivreClient.get_item` for each, copies `available_quantity` (or
the variation slot) into `product_links.stock`, and writes a `SyncLog` row so
the operation is auditable. Does NOT push back to ML — read-only repair.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BackgroundJob,
    BackgroundJobStatus,
    Integration,
    IntegrationPlatform,
    LinkSyncStatus,
    ProductLink,
    SyncLog,
    SyncLogAction,
)
from app.security.cipher import decrypt_json, encrypt_json
from app.services.marketplaces.ml import MercadoLivreClient

logger = structlog.get_logger()


async def run_backfill_ml_stock(
    session: AsyncSession,
    *,
    job_id: UUID,
    user_id: UUID,
) -> dict[str, int]:
    """Returns counters {scanned, repaired, skipped, errored}."""
    job = await session.get(BackgroundJob, job_id)
    if job is None:
        raise LookupError(f"job_not_found: {job_id}")
    job.status = BackgroundJobStatus.RUNNING
    job.started_at = datetime.now(UTC)
    await session.commit()

    links = (
        await session.execute(
            select(ProductLink).where(
                and_(
                    ProductLink.user_id == user_id,
                    ProductLink.platform == IntegrationPlatform.ML,
                    or_(
                        ProductLink.last_sync_at.is_(None),
                        and_(
                            ProductLink.stock == 0,
                            ProductLink.last_sync_status != LinkSyncStatus.OK,
                        ),
                    ),
                )
            )
        )
    ).scalars().all()

    counters = {"scanned": len(links), "repaired": 0, "skipped": 0, "errored": 0}
    job.total = counters["scanned"]
    await session.commit()

    integ_cache: dict[UUID, Integration] = {}
    client_cache: dict[UUID, MercadoLivreClient] = {}

    for idx, link in enumerate(links, start=1):
        integ = integ_cache.get(link.integration_id)
        if integ is None:
            integ = await session.get(Integration, link.integration_id)
            if integ is None:
                counters["errored"] += 1
                _log(session, user_id, job_id, link, "integration_missing", None)
                continue
            integ_cache[link.integration_id] = integ

        client = client_cache.get(integ.id)
        if client is None:
            creds = decrypt_json(integ.credentials)

            async def _persist(new_creds: dict, _integ=integ) -> None:
                _integ.credentials = encrypt_json(new_creds)
                exp = new_creds.get("expires_at")
                if exp:
                    _integ.token_expires_at = datetime.fromtimestamp(int(exp), tz=UTC)
                await session.commit()

            client = MercadoLivreClient(creds, on_token_refresh=_persist)
            client_cache[integ.id] = client

        try:
            item = await client.get_item(link.external_id)
        except Exception as e:  # noqa: BLE001
            counters["errored"] += 1
            _log(session, user_id, job_id, link, "ml_get_item_failed", str(e)[:300])
            continue

        new_qty = _qty_from_item(item, link.variation_id)
        if new_qty is None:
            counters["skipped"] += 1
            _log(session, user_id, job_id, link, "ml_qty_not_found", None)
            continue

        before = link.stock
        link.stock = int(new_qty)
        link.last_sync_at = datetime.now(UTC)
        # Stock-only refresh; preserve previous status semantics.
        if link.last_sync_status in (LinkSyncStatus.PENDING, LinkSyncStatus.RETRYABLE):
            link.last_sync_status = LinkSyncStatus.OK
        link.last_error = None

        session.add(
            SyncLog(
                user_id=user_id,
                job_id=job_id,
                product_id=link.product_id,
                product_link_id=link.id,
                integration_id=link.integration_id,
                store_id=link.store_id,
                platform=IntegrationPlatform.ML,
                action=SyncLogAction.UPDATE_STOCK,
                status=LinkSyncStatus.OK,
                qty_before=before,
                qty_after=int(new_qty),
                payload={"source": "ml_backfill", "item_id": link.external_id},
            )
        )
        counters["repaired"] += 1
        if idx % 25 == 0:
            job.processed = idx
            job.last_heartbeat_at = datetime.now(UTC)
            await session.commit()

    job.processed = counters["scanned"]
    job.status = BackgroundJobStatus.SUCCEEDED
    job.finished_at = datetime.now(UTC)
    job.result = counters
    await session.commit()
    logger.info("ml_backfill_done", **counters)
    return counters


def _qty_from_item(item: dict, variation_id: str | None) -> int | None:
    if variation_id:
        for v in item.get("variations") or []:
            if str(v.get("id")) == str(variation_id):
                q = v.get("available_quantity")
                return int(q) if q is not None else None
    q = item.get("available_quantity")
    return int(q) if q is not None else None


def _log(
    session: AsyncSession,
    user_id: UUID,
    job_id: UUID,
    link: ProductLink,
    error_code: str,
    error_detail: str | None,
) -> None:
    session.add(
        SyncLog(
            user_id=user_id,
            job_id=job_id,
            product_id=link.product_id,
            product_link_id=link.id,
            integration_id=link.integration_id,
            store_id=link.store_id,
            platform=IntegrationPlatform.ML,
            action=SyncLogAction.UPDATE_STOCK,
            status=LinkSyncStatus.SKIPPED,
            qty_before=link.stock,
            error_code=error_code,
            error_detail=error_detail,
            payload={"source": "ml_backfill"},
        )
    )
