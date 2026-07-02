"""Sync router (Fase 4a).

Endpoints:
    POST /api/jobs/sync-all       — enqueue full sync run
    POST /api/sync/product/{id}   — sync one product (synchronous)
    GET  /api/sync-logs           — paginated logs
    GET  /api/sync-logs/stats     — last-window aggregates
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_active_user, require_permission, user_scope
from app.models import (
    BackgroundJob,
    BackgroundJobStatus,
    BackgroundJobType,
    IntegrationPlatform,
    LinkSyncStatus,
    Product,
    ProductLink,
    SyncLog,
    User,
)
from app.schemas.products import JobCreatedOut, JobOut
from app.schemas.sync import (
    SyncAllIn,
    SyncLogOut,
    SyncLogPage,
    SyncLogStats,
    SyncProductBody,
)
from app.services.advisory_lock import SYNC_NAMESPACE, _user_lock_key, try_user_sync_lock
from app.services.job_details import append_job_detail, load_job_details_tail
from app.services.link_reconcile import reconcile_product_links
from app.services.sync_orchestrator import SyncOrchestrator
from app.worker_pool import get_arq_pool

logger = structlog.get_logger()
router = APIRouter(prefix="/api", tags=["sync"])


@router.post(
    "/jobs/sync-all",
    response_model=JobCreatedOut,
    status_code=status.HTTP_201_CREATED,
)
async def enqueue_sync_all(
    body: SyncAllIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("produtos", "edit"))],
) -> JobCreatedOut:
    job = BackgroundJob(
        type=BackgroundJobType.SYNC_ALL,
        status=BackgroundJobStatus.PENDING,
        created_by=user.id,
        payload={
            "integration_ids": [str(i) for i in (body.integration_ids or [])],
            "product_ids": [str(p) for p in (body.product_ids or [])],
            "include_all_stock": bool(body.include_all_stock),
        },
    )
    session.add(job)
    await session.flush()

    pool = await get_arq_pool()
    arq = await pool.enqueue_job(
        "sync_all_run",
        str(job.id),
        str(user.id),
        body.product_ids and [str(p) for p in body.product_ids],
        bool(body.include_all_stock),
    )
    if arq is not None:
        job.arq_job_id = arq.job_id
    await session.commit()
    return JobCreatedOut(job_id=job.id)


@router.post("/sync/reset-lock")
async def reset_sync_lock(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("produtos", "edit"))],
    force: bool = Query(False, description="If true, pg_terminate_backend on holders"),
) -> dict:
    """Inspect (and optionally clear) the per-user sync advisory lock.

    Mirrors SSH's `POST /api/sync/reset-lock`: in DaVinci the lock is a
    Postgres advisory lock keyed on `(SYNC_NAMESPACE, hash(user_id))`. It
    auto-releases when the holding session is checked back into the pool,
    so a stuck lock usually means a wedged worker connection. With
    `force=true`, we `pg_terminate_backend` the holders — last resort.
    """
    key = _user_lock_key(user.id)
    row = await session.execute(
        text(
            "SELECT pid, granted, mode FROM pg_locks "
            "WHERE locktype = 'advisory' "
            "AND classid = :ns AND objid = ((:k)::bigint & 4294967295)::int"
        ),
        {"ns": SYNC_NAMESPACE, "k": key},
    )
    holders = [{"pid": r.pid, "granted": r.granted, "mode": r.mode} for r in row.all()]

    terminated: list[int] = []
    if force and holders:
        for h in holders:
            try:
                await session.execute(
                    text("SELECT pg_terminate_backend(:pid)"), {"pid": h["pid"]}
                )
                terminated.append(h["pid"])
            except Exception as e:  # noqa: BLE001
                logger.warning("reset_sync_lock_term_failed", pid=h["pid"], err=str(e))

    return {
        "user_id": str(user.id),
        "key": key,
        "holders": holders,
        "forced": force,
        "terminated": terminated,
    }


@router.post(
    "/jobs/backfill-ml-stock",
    response_model=JobCreatedOut,
    status_code=status.HTTP_201_CREATED,
)
async def enqueue_backfill_ml_stock(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("produtos", "edit"))],
) -> JobCreatedOut:
    """One-shot repair (B2): re-fetch ML stock for links with stock=0 or
    last_sync_at=NULL. No outbound push."""
    job = BackgroundJob(
        type=BackgroundJobType.BACKFILL_ML_STOCK,
        status=BackgroundJobStatus.PENDING,
        created_by=user.id,
        payload={},
    )
    session.add(job)
    await session.flush()

    pool = await get_arq_pool()
    arq = await pool.enqueue_job("ml_backfill_run", str(job.id), str(user.id))
    if arq is not None:
        job.arq_job_id = arq.job_id
    await session.commit()
    return JobCreatedOut(job_id=job.id)


@router.post(
    "/jobs/refresh-bling-stock",
    response_model=JobCreatedOut,
    status_code=status.HTTP_201_CREATED,
)
async def enqueue_refresh_bling_stock(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("produtos", "edit"))],
) -> JobCreatedOut:
    """Manual quick path: pull stock from Bling /produtos page-by-page (100)
    and write to local products + product_links. No marketplace push."""
    job = BackgroundJob(
        type=BackgroundJobType.REFRESH_BLING_STOCK,
        status=BackgroundJobStatus.PENDING,
        created_by=user.id,
        payload={},
    )
    session.add(job)
    await session.flush()

    pool = await get_arq_pool()
    arq = await pool.enqueue_job("refresh_bling_stock_run", str(job.id))
    if arq is not None:
        job.arq_job_id = arq.job_id
    await session.commit()
    return JobCreatedOut(job_id=job.id)


@router.post("/sync/product/{product_id}", response_model=JobOut)
async def sync_product(
    product_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("produtos", "edit"))],
    body: SyncProductBody | None = None,
) -> JobOut:
    product = (
        await session.execute(
            select(Product).where(
                and_(Product.id == product_id, user_scope(Product, user))
            )
        )
    ).scalar_one_or_none()
    if product is None:
        raise HTTPException(404, detail={"code": "product_not_found"})

    requested_integration_ids = body.integration_ids if body else None
    do_reconcile = body.reconcile if body else True

    # Snapshot the clicked product's link ids (respecting the integration
    # filter) BEFORE reconcile — these are the links the operator asked to
    # reload, wherever they end up living after a re-point.
    link_q = select(ProductLink.id).where(ProductLink.product_id == product.id)
    if requested_integration_ids:
        link_q = link_q.where(
            ProductLink.integration_id.in_(requested_integration_ids)
        )
    original_link_ids = list((await session.execute(link_q)).scalars().all())
    if requested_integration_ids and not original_link_ids:
        raise HTTPException(400, detail={"code": "no_links_for_integrations"})

    job = BackgroundJob(
        type=BackgroundJobType.SYNC_PRODUCT,
        status=BackgroundJobStatus.PENDING,
        created_by=user.id,
        payload={
            "product_id": str(product_id),
            "integration_ids": [str(i) for i in (requested_integration_ids or [])],
        },
    )
    session.add(job)
    await session.flush()

    # --- On-demand SKU reconcile (ONLY here, never in sync-all/auto-link) ---
    # Re-read each in-scope listing's current seller_sku and move any link
    # whose SKU now belongs to a different product. Report every move/warning
    # into the job's detail log so the toast shows what happened.
    only_link_ids: list[UUID] | None = (
        list(original_link_ids) if requested_integration_ids else None
    )
    products_to_sync: list[Product] = [product]
    reconcile_summary: dict | None = None
    if do_reconcile:
        report = await reconcile_product_links(
            session,
            user=user,
            product=product,
            only_integration_ids=requested_integration_ids,
        )
        for m in report.moves:
            await append_job_detail(
                session,
                job.id,
                {
                    "kind": "reconcile_move",
                    "link_id": str(m.link_id),
                    "integration_id": str(m.integration_id),
                    "platform": m.platform,
                    "external_id": m.external_id,
                    "from_sku": m.from_sku,
                    "to_sku": m.to_sku,
                },
            )
        for w in report.warnings:
            await append_job_detail(session, job.id, {"kind": "reconcile_warning", **w})
        for dup_id in report.excedent_deleted:
            await append_job_detail(
                session,
                job.id,
                {"kind": "reconcile_excedent_deleted", "link_id": str(dup_id)},
            )
        reconcile_summary = {
            "checked": report.checked,
            "moved": len(report.moves),
            "excedent_deleted": len(report.excedent_deleted),
            "warnings": len(report.warnings),
            "unreadable": report.unreadable,
        }
        if report.moves:
            # Links moved to other products — sync those too, scoped to the
            # moved links plus each target's Bling link so its stock refreshes
            # BEFORE the marketplace push (so the moved listing gets the right
            # product's fresh stock, not a stale value).
            sync_ids = set(original_link_ids) - set(report.excedent_deleted)
            target_ids = report.moved_product_ids
            bling_ids = (
                await session.execute(
                    select(ProductLink.id).where(
                        and_(
                            ProductLink.product_id.in_(target_ids),
                            ProductLink.platform == IntegrationPlatform.BLING,
                        )
                    )
                )
            ).scalars().all()
            sync_ids.update(bling_ids)
            only_link_ids = list(sync_ids)
            extra = (
                await session.execute(
                    select(Product).where(
                        and_(
                            Product.id.in_(target_ids),
                            user_scope(Product, user),
                        )
                    )
                )
            ).scalars().all()
            products_to_sync = [product, *extra]

    # Individual sync intentionally bypasses the per-user advisory lock that
    # `sync_all` uses: this endpoint runs synchronously, is scoped to one
    # product, and the user expects to be able to click sync on several
    # products without seeing `sync_already_running`. The orchestrator's
    # per-link writes are still safe under concurrent runs — the worst case
    # is two pushes of the same value to the same marketplace.
    # `force=True` also bypasses ML's B1 zero-guard: the operator clicked
    # sync expecting the marketplace to mirror whatever Bling reports, even
    # when that means dropping from positive stock to zero.
    # `force_bling_refresh=True` bypasses the 24h Bling stock cache so the
    # operator sees the up-to-date stock pushed, not whatever Redis had.
    orch = SyncOrchestrator(
        session,
        user_id=user.id,
        job=job,
        force=True,
        force_bling_refresh=True,
    )
    await orch.run(products_to_sync, only_link_ids=only_link_ids)

    # orch.run() overwrites job.result with the push summary — merge the
    # reconcile summary back in AFTERWARD so the response carries both.
    if reconcile_summary is not None:
        job.result = {**(job.result or {}), "reconcile": reconcile_summary}
        await session.commit()

    await session.refresh(job)
    out = JobOut.model_validate(job, from_attributes=True)
    # Per-link detail now lives in `background_job_details` (off the hot job
    # row). Hydrate the response from there so the front's per-link toast keeps
    # working — a single product's log is a handful of entries.
    out.details = await load_job_details_tail(session, job.id)
    return out


@router.get("/sync-logs", response_model=SyncLogPage)
async def list_sync_logs(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_active_user)],
    platform: str | None = Query(None),
    status_: str | None = Query(None, alias="status"),
    sku: str | None = Query(None),
    product_id: UUID | None = Query(None),
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> SyncLogPage:
    where = [user_scope(SyncLog, user)]
    if platform:
        where.append(SyncLog.platform == platform)
    if status_:
        where.append(SyncLog.status == status_)
    if product_id:
        where.append(SyncLog.product_id == product_id)
    if since:
        where.append(SyncLog.created_at >= since)
    if until:
        where.append(SyncLog.created_at < until)
    if sku:
        where.append(
            SyncLog.product_id.in_(
                select(Product.id).where(
                    and_(user_scope(Product, user), Product.sku == sku)
                )
            )
        )

    total = (
        await session.execute(select(func.count()).select_from(SyncLog).where(and_(*where)))
    ).scalar_one()
    rows = (
        await session.execute(
            select(SyncLog)
            .where(and_(*where))
            .order_by(SyncLog.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()
    return SyncLogPage(
        items=[SyncLogOut.model_validate(r, from_attributes=True) for r in rows],
        total=int(total),
        limit=limit,
        offset=offset,
    )


@router.get("/sync-logs/stats", response_model=SyncLogStats)
async def sync_logs_stats(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_active_user)],
    window_hours: int = Query(24, ge=1, le=168),
) -> SyncLogStats:
    cutoff = datetime.now(UTC) - timedelta(hours=window_hours)
    rows = (
        await session.execute(
            select(SyncLog.platform, SyncLog.status, func.count())
            .where(
                and_(
                    user_scope(SyncLog, user),
                    SyncLog.created_at >= cutoff,
                )
            )
            .group_by(SyncLog.platform, SyncLog.status)
        )
    ).all()

    totals = {s.value: 0 for s in LinkSyncStatus}
    by_platform: dict[str, dict[str, int]] = {}
    for plat, st, n in rows:
        plat_key = plat.value if plat is not None else "unknown"
        st_key = st.value
        totals[st_key] = totals.get(st_key, 0) + int(n)
        by_platform.setdefault(plat_key, {})[st_key] = int(n)

    return SyncLogStats(
        window_hours=window_hours,
        ok=totals.get("ok", 0),
        skipped=totals.get("skipped", 0),
        retryable=totals.get("retryable", 0),
        fatal=totals.get("fatal", 0),
        requires_review=totals.get("requires_review", 0),
        by_platform=by_platform,
    )


__all__ = ["router", "session_scope"]
