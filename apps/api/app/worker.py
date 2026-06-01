from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

import structlog
from arq import cron
from arq.connections import RedisSettings
from sqlalchemy import and_, delete, or_, select, text, update

from app.config import get_settings
from app.db import session_scope
from app.models import (
    Alert,
    AlertSeverity,
    AlertType,
    AuthCode,
    BackgroundJob,
    BackgroundJobStatus,
    BackgroundJobType,
    Integration,
    IntegrationPlatform,
    Product,
    UserSettings,
)
from app.redis_client import redis
from app.security.cipher import decrypt_json, encrypt_json
from app.services.advisory_lock import release_stale_sync_locks, try_user_sync_lock
from app.services.alerts import emit_alert
from app.services.audit.runner import run_audit
from app.services.auto_link import run_auto_link
from app.services.bling_kit_create import create_bling_kit_for_mark_job
from app.services.bling_orders import run_ingest_bling_order
from app.services.bling_product_create import run_auto_create_product_from_bling
from app.services.import_product_bling_create import sync_import_product_to_bling_job
from app.services.email import get_email_sender, render_otp_html
from app.services.listings_import import (
    _create_product_links_for_matched,
    _link_by_sku,
    run_auto_import_link,
    run_import_listings,
)
from app.services.marketplace_financials import (
    run_due_marketplace_financial_retries,
    run_sync_marketplace_financials_for_bling_order,
)
from app.services.refunds_freight_sync import backfill_freight_refunds
from app.services.marketplace_shipment_check import run_check_marketplace_shipped_orders
from app.services.marketplaces.bling import BlingClient
from app.services.marketplaces.ml import MercadoLivreClient
from app.services.marketplaces.shopee import ShopeeClient
from app.services.ml_backfill import run_backfill_ml_stock
from app.services.pricing.batch import run_push_prices_batch
from app.services.pricing.cost_sync import run_sync_bling_costs
from app.services.product_cost_sync import run_sync_product_bling_costs
from app.services.refresh_bling_stock import run_refresh_bling_stock
from app.services.sync_orchestrator import SyncOrchestrator
from app.worker_pool import get_arq_pool

logger = structlog.get_logger()
_settings = get_settings()

# Brasília is UTC-3 (no DST since 2019). Cron schedules run in UTC: BRT = UTC-3.
SP_TZ = ZoneInfo("America/Sao_Paulo")

# Daily `sync_all_run` only processes products at risk of stockout. Items
# with `products.stock >= 10` are skipped — webhooks keep their stock fresh
# inline anyway, and high-stock items rarely cause real-world divergence.
SYNC_ALL_LOW_STOCK_THRESHOLD = 10


async def send_otp_email(ctx: dict, *, email: str, prefix: str, code: str, ttl_minutes: int) -> None:
    sender = get_email_sender()
    html = render_otp_html(prefix=prefix, code=code, ttl_minutes=ttl_minutes)
    text_body = (
        f"DaVinci\n\n"
        f"Confirme o prefixo: {prefix}\n"
        f"Código: {code}\n"
        f"Expira em {ttl_minutes} minutos.\n"
    )
    await sender.send(
        to=email,
        subject=f"DaVinci — Código {prefix}",
        html=html,
        text=text_body,
    )


async def auth_codes_cleanup(ctx: dict) -> None:
    cutoff = datetime.now(UTC) - timedelta(days=7)
    async with session_scope() as s:
        result = await s.execute(delete(AuthCode).where(AuthCode.expires_at < cutoff))
        logger.info("auth_codes_cleanup_done", deleted=result.rowcount or 0)


async def auto_link_run(
    ctx: dict,
    job_id: str,
    integration_ids: list[str] | None,
) -> None:
    async with session_scope() as s:
        await run_auto_link(
            s,
            job_id=UUID(job_id),
            integration_ids=[UUID(i) for i in (integration_ids or [])] or None,
        )


async def sync_all_run(
    ctx: dict,
    job_id: str,
    user_id: str,
    product_ids: list[str] | None,
    include_all_stock: bool = False,
) -> None:
    """Fase 4a: full sync run. Acquires per-user advisory lock; if busy, marks
    job as `failed` with `error='sync_already_running'`.

    `include_all_stock=True` bypasses the cron-driven low-stock filter — used
    when the UI explicitly clicks "sync all" and the user expects every
    product to be pushed to its marketplaces.
    """
    uid = UUID(user_id)
    jid = UUID(job_id)

    async with session_scope() as s:
        async with try_user_sync_lock(s, uid) as acquired:
            if not acquired:
                job = await s.get(BackgroundJob, jid)
                if job is not None:
                    job.status = BackgroundJobStatus.FAILED
                    job.error = "sync_already_running"
                    job.finished_at = datetime.now(UTC)
                logger.warning("sync_all_run_locked", user_id=user_id, job_id=job_id)
                return

            job = await s.get(BackgroundJob, jid)
            if job is None:
                logger.warning("sync_all_run_job_missing", job_id=job_id)
                return

            where: list = []
            if product_ids:
                where.append(Product.id.in_([UUID(p) for p in product_ids]))
            elif not include_all_stock:
                # Low-stock-only mode: cron-driven sync_all sweeps only items
                # at risk of stockout. Hi-stock items rarely diverge between
                # Bling and marketplaces; skipping them keeps the per-day
                # call volume well under Bling's CF rate gate. Manual full
                # sync (UI button) sets include_all_stock=True to bypass.
                where.append(Product.stock < SYNC_ALL_LOW_STOCK_THRESHOLD)
            stmt = select(Product)
            if where:
                stmt = stmt.where(and_(*where))
            products = (await s.execute(stmt)).scalars().all()
            pids = [p.id for p in products]
            # Count links upfront so processed/total stays a real ratio
            # (the orchestrator bumps `processed` once per link). The
            # product count goes into payload so the UI can show both.
            if pids:
                link_total = (
                    await s.execute(
                        text(
                            "SELECT COUNT(*) FROM davinci.product_links "
                            "WHERE product_id = ANY(:pids)"
                        ),
                        {"pids": [str(p) for p in pids]},
                    )
                ).scalar_one()
            else:
                link_total = 0
            job.total = int(link_total)
            job.payload = {**(job.payload or {}), "total_products": len(products)}
            await s.commit()

            # `force_bling_refresh=True`: daily sync_all also bypasses the
            # cached-refresh shortcut so every product gets fresh Bling stock
            # before pushing to marketplaces. (TTL is now 0 anyway, but the
            # flag stays explicit so the intent is clear if the constant
            # changes later.)
            orch = SyncOrchestrator(
                s, user_id=uid, job=job, force_bling_refresh=True
            )
            # SSH delta #1 — run_with_retry wraps run_parallel with up to
            # MAX_RESYNC_ROUNDS-1 retry passes for products whose links
            # came back RETRYABLE. The verify-before-send shortcut (delta
            # #2) and skipped_verified accounting are inside _process_link.
            report = await orch.run_with_retry(pids)

            if (job.payload or {}).get("trigger") == "daily_sync":
                await _notify_daily_sync_completed(s, uid, job, report)


async def _notify_daily_sync_completed(s, user_id: UUID, job, report) -> None:
    """Emit `daily_sync_completed` alert + optional Telegram message."""
    us = await s.get(UserSettings, user_id)
    if us is None or not us.notify_daily_sync:
        return
    severity = AlertSeverity.SUCCESS if report.fatal == 0 else AlertSeverity.WARNING
    title = "Sync diário concluído"
    msg = (
        f"Total {report.total_links} links — "
        f"{report.ok} ok, {report.skipped} skipped, "
        f"{report.retryable} retry, {report.fatal} fatal, "
        f"{report.requires_review} review."
    )
    await emit_alert(
        s,
        user_id=user_id,
        type=AlertType.DAILY_SYNC_COMPLETED,
        severity=severity,
        title=title,
        message=msg,
        payload={
            "job_id": str(job.id),
            "total_links": report.total_links,
            "ok": report.ok,
            "skipped": report.skipped,
            "retryable": report.retryable,
            "fatal": report.fatal,
            "requires_review": report.requires_review,
        },
        dedupe_key=f"daily_sync:{user_id}:{datetime.now(SP_TZ).date().isoformat()}",
        notify_telegram=False,
    )
    if us.notify_telegram:
        from app.services.telegram import TelegramClient
        tg = TelegramClient()
        await tg.safe_send(
            f"<b>DaVinci — Sync diário</b>\n{msg}",
            chat_id=us.telegram_chat_id,
        )


async def sync_product_run(
    ctx: dict,
    job_id: str,
    user_id: str,
    product_id: str,
    link_ids: list[str] | None,
) -> None:
    """Fase 5: targeted sync triggered by Bling webhook. Per-product advisory
    lock keeps concurrent webhook deliveries serialized for the same product
    while still allowing parallelism across products."""
    uid = UUID(user_id)
    pid = UUID(product_id)
    jid = UUID(job_id)

    lock_key = f"sync_product:{pid}"
    async with session_scope() as s:
        await s.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": lock_key}
        )
        job = await s.get(BackgroundJob, jid)
        product = await s.get(Product, pid)
        if job is None or product is None:
            logger.warning(
                "sync_product_run_missing", job_id=job_id, product_id=product_id
            )
            return
        link_uuids = [UUID(i) for i in (link_ids or [])] or None
        # Webhook-triggered single-product sync: the payload carries the
        # current Bling stock (often 0 on sellout), so we trust it and
        # bypass both the ML B1 zero-guard and the 24h Bling refresh cache
        # — the webhook itself is the "fresh stock" signal.
        orch = SyncOrchestrator(
            s, user_id=uid, job=job, force=True, force_bling_refresh=True
        )
        await orch.run([product], only_link_ids=link_uuids)


async def ml_backfill_run(
    ctx: dict,
    job_id: str,
    user_id: str,
) -> None:
    async with session_scope() as s:
        await run_backfill_ml_stock(
            s,
            job_id=UUID(job_id),
            user_id=UUID(user_id),
        )


async def refresh_bling_stock_run(
    ctx: dict,
    job_id: str,
) -> None:
    """Manual stock-only refresh: paginates Bling /produtos and writes stock
    to local product_links + products. No marketplace push."""
    async with session_scope() as s:
        await run_refresh_bling_stock(s, job_id=UUID(job_id))


async def auto_create_product_from_bling_run(
    ctx: dict,
    bling_product_id: int,
    user_id: str,
) -> None:
    """Lazily create a local Product from a Bling webhook for a product we
    never imported. Single-tenant attribution: the caller resolves the user
    by the Bling integration owner."""
    async with session_scope() as s:
        await run_auto_create_product_from_bling(
            s,
            bling_product_id=int(bling_product_id),
            user_id=UUID(user_id),
        )


async def ingest_bling_order_run(
    ctx: dict,
    bling_order_id: int,
    user_id: str,
    event: str | None = None,
) -> None:
    """Fase 5b: ingest a Bling pedido de venda triggered by webhook.

    Per-order advisory lock keeps concurrent webhook deliveries serialized
    for the same order while still allowing parallelism across orders.
    """
    uid = UUID(user_id)
    lock_key = f"ingest_bling_order:{bling_order_id}"
    async with session_scope() as s:
        await s.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": lock_key}
        )
        await run_ingest_bling_order(
            s,
            bling_order_id=int(bling_order_id),
            user_id=uid,
            event=event,
        )


async def sync_marketplace_financials_for_order_run(
    ctx: dict,
    bling_order_id: int,
    trigger: str = "manual",
) -> None:
    """Fetch marketplace financials for one Bling order after ingestion."""
    lock_key = f"marketplace_financials:{bling_order_id}"
    async with session_scope() as s:
        await s.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": lock_key}
        )
        await run_sync_marketplace_financials_for_bling_order(
            s,
            bling_order_id=int(bling_order_id),
            trigger=trigger,
        )


# ---------------------------------------------------------------- cron jobs


async def marketing_agent_cycle(ctx: dict) -> None:
    """Every 15 min: run one decision cycle per enabled MarketingAccount.

    Gated by `settings.enable_marketing`. When the flag is off the cron is
    a no-op so prod (where the tables don't exist yet) doesn't churn errors
    every quarter-hour. Imports are local so the worker boots cleanly even
    if marketing models change shape.
    """
    if not _settings.enable_marketing:
        return
    from app.models.marketing import MarketingAccount
    from app.services.marketing.agent import (
        agent_decision_cycle as _marketing_run_cycle,
    )

    async with session_scope() as s:
        rows = (
            await s.execute(
                select(MarketingAccount).where(MarketingAccount.agent_enabled.is_(True))
            )
        ).scalars().all()
        ids = [a.id for a in rows]
    for aid in ids:
        try:
            await _marketing_run_cycle(aid)
        except Exception as e:  # noqa: BLE001
            logger.error("marketing_agent_cycle_error", account_id=str(aid), err=str(e)[:200])


async def marketing_full_sync(ctx: dict) -> None:
    """Every 30 min: pull live ML + Amazon Ads numbers and persist them
    into the marketing_* tables.

    Shopee is intentionally NOT included here — its per-partner Ads
    throttle is so tight that batching 13 shops never succeeds. Shopee
    runs on its own 5-min round-robin cron (`marketing_shopee_tick`)
    that processes one shop per tick.

    Each platform runs in its own try/except so a slow Amazon report
    doesn't abort ML. Feature flag (`enable_marketing`) keeps prod
    inert when off.
    """
    if not _settings.enable_marketing:
        return
    from app.services.marketing.amazon_sync import sync_all_amazon_integrations
    from app.services.marketing.ml_sync import sync_all_ml_integrations

    async with session_scope() as s:
        results: dict[str, list[dict] | str] = {}
        for name, runner in (
            ("mercadolivre", sync_all_ml_integrations),
            ("amazon", sync_all_amazon_integrations),
        ):
            try:
                results[name] = await runner(s)
            except Exception as e:  # noqa: BLE001
                logger.error(
                    "marketing_full_sync_platform_failed",
                    platform=name, err=str(e)[:300],
                )
                results[name] = f"error: {str(e)[:200]}"
    summary = {
        k: (len(v) if isinstance(v, list) else 0) for k, v in results.items()
    }
    logger.info("marketing_full_sync", **summary)


async def marketing_shopee_tick(ctx: dict) -> None:
    """Every 5 min: sync ONE Shopee shop (the one with the oldest
    `last_ads_sync_at`). Designed around Shopee's per-partner Ads
    throttle which fails any batch over a few calls. With 13 shops × 5min
    cron, each shop refreshes ~once per 65 minutes — well under the
    throttle ceiling.

    Double-gated by `enable_marketing` (turns off the whole module) and
    `enable_shopee_ads` (turns off ONLY Shopee while keeping ML/Amazon
    running — useful while a Shopee Open Platform quota ticket is open).
    """
    if not _settings.enable_marketing or not _settings.enable_shopee_ads:
        return
    from app.services.marketing.shopee_sync import sync_shopee_single_next

    async with session_scope() as s:
        try:
            r = await sync_shopee_single_next(s)
        except Exception as e:  # noqa: BLE001
            logger.error("marketing_shopee_tick_failed", err=str(e)[:300])
            return
    logger.info(
        "marketing_shopee_tick",
        status=r.get("status"),
        integration_id=r.get("integration_id"),
        name=r.get("name"),
    )


async def daily_sync_scheduler(ctx: dict) -> None:
    """Every 5min: enqueue sync_all for users whose `daily_sync_time` falls
    inside the current 5-minute window in America/Sao_Paulo, only if no
    sync_all job has been created for that user since BRT midnight."""
    now_sp = datetime.now(SP_TZ).replace(second=0, microsecond=0)
    window_start = (now_sp - timedelta(minutes=5)).time()
    window_end = now_sp.time()
    sp_midnight = now_sp.replace(hour=0, minute=0)
    today_cutoff_utc = sp_midnight.astimezone(UTC)

    async with session_scope() as s:
        rows = await s.execute(
            select(UserSettings).where(
                UserSettings.daily_sync_enabled.is_(True),
                UserSettings.daily_sync_time.is_not(None),
                UserSettings.daily_sync_time >= window_start,
                UserSettings.daily_sync_time <= window_end,
            )
        )
        for us in rows.scalars():
            already = (
                await s.execute(
                    select(BackgroundJob.id).where(
                        BackgroundJob.created_by == us.user_id,
                        BackgroundJob.type == BackgroundJobType.SYNC_ALL,
                        BackgroundJob.created_at >= today_cutoff_utc,
                    )
                )
            ).first()
            if already is not None:
                continue
            job = BackgroundJob(
                type=BackgroundJobType.SYNC_ALL,
                status=BackgroundJobStatus.PENDING,
                created_by=us.user_id,
                payload={"trigger": "daily_sync"},
            )
            s.add(job)
            await s.flush()
            pool = await get_arq_pool()
            arq = await pool.enqueue_job(
                "sync_all_run", str(job.id), str(us.user_id), None
            )
            if arq is not None:
                job.arq_job_id = arq.job_id
            logger.info(
                "daily_sync_enqueued", user_id=str(us.user_id), job_id=str(job.id)
            )


async def product_bling_cost_sync(ctx: dict) -> None:
    """Diário: atualiza `products.bling_cost_price` de TODOS os produtos a
    partir da listagem `/produtos` do Bling (a lista traz precoCusto; o
    detalhe /produtos/{id} não). Os pedidos snapshotam esse custo ao entrar
    em situacao=6, então o refresh diário mantém o custo de cada pedido novo
    atualizado."""
    async with session_scope() as s:
        summary = await run_sync_product_bling_costs(s)
    logger.info("product_bling_cost_sync_done", **summary)


async def _refresh_tokens_for(platform: IntegrationPlatform, *, expiring_within_s: int) -> None:
    cutoff = datetime.now(UTC) + timedelta(seconds=expiring_within_s)
    async with session_scope() as s:
        ints = (
            await s.execute(
                select(Integration).where(
                    Integration.platform == platform,
                    Integration.token_expires_at.is_not(None),
                    Integration.token_expires_at <= cutoff,
                )
            )
        ).scalars().all()
        for it in ints:
            try:
                creds = decrypt_json(it.credentials)

                async def _persist(new_creds: dict, _it=it, _s=s) -> None:
                    _it.credentials = encrypt_json(new_creds)
                    exp = new_creds.get("expires_at")
                    if exp:
                        _it.token_expires_at = datetime.fromtimestamp(int(exp), tz=UTC)
                    await _s.commit()

                if platform == IntegrationPlatform.BLING:
                    client = BlingClient(creds, integration_id=it.id)
                elif platform == IntegrationPlatform.SHOPEE:
                    client = ShopeeClient(creds, on_token_refresh=_persist)
                elif platform == IntegrationPlatform.ML:
                    client = MercadoLivreClient(creds, on_token_refresh=_persist)
                elif platform == IntegrationPlatform.TIKTOK:
                    from app.services.marketplaces.tiktok import TikTokClient
                    client = TikTokClient(creds, on_token_refresh=_persist)
                else:
                    continue
                await client.refresh()
                logger.info(
                    "token_refresh_ok",
                    platform=platform.value,
                    integration_id=str(it.id),
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "token_refresh_failed",
                    platform=platform.value,
                    integration_id=str(it.id),
                    err=str(e),
                )


async def bling_token_refresh(ctx: dict) -> None:
    """Refresh Bling tokens expiring within 30min. Bling AT lasts 6h, so a
    single refresh per cycle is enough; the hourly cron + 30min window means
    at most one /oauth/token call per access token lifetime."""
    await _refresh_tokens_for(IntegrationPlatform.BLING, expiring_within_s=1800)


async def shopee_token_refresh(ctx: dict) -> None:
    """Refresh Shopee tokens expiring within 6h (Shopee tokens last 4h)."""
    await _refresh_tokens_for(IntegrationPlatform.SHOPEE, expiring_within_s=6 * 3600)


async def ml_token_refresh(ctx: dict) -> None:
    """Refresh Mercado Livre tokens expiring within 2h (ML access tokens last 6h)."""
    await _refresh_tokens_for(IntegrationPlatform.ML, expiring_within_s=2 * 3600)


async def tiktok_token_refresh(ctx: dict) -> None:
    """Refresh TikTok tokens expiring within 12h (TikTok AT lasts ~24h)."""
    await _refresh_tokens_for(IntegrationPlatform.TIKTOK, expiring_within_s=12 * 3600)


async def refunds_freight_backfill(ctx: dict) -> None:
    """Sweep the full margens view and upsert Frete refunds.

    Catches reconciliations that close days after the order (ML freight
    diffs in particular). Per-order hook in marketplace_financials covers
    the realtime case; this is the safety net.
    """
    async with session_scope() as s:
        result = await backfill_freight_refunds(s)
    logger.info("refunds_freight_backfill_cron_done", **result)


async def marketplace_financials_retry(ctx: dict) -> None:
    """Retry marketplace financial lookups that were not available on webhook."""
    async with session_scope() as s:
        result = await run_due_marketplace_financial_retries(s, limit=100)
    logger.info("marketplace_financials_retry_done", **result)


async def shopee_discrepancy_check(ctx: dict) -> None:
    """Compare Shopee stock vs local DB and fix discrepancies.
    Runs every 4h to catch phantom stock issues."""
    from app.services.shopee_discrepancy_check import run_shopee_discrepancy_check
    try:
        result = await run_shopee_discrepancy_check()
        logger.info("shopee_discrepancy_check_done", **result)
    except Exception as e:  # noqa: BLE001
        logger.error("shopee_discrepancy_check_failed", error=str(e))


async def ml_discrepancy_check(ctx: dict) -> None:
    """Compare Mercado Livre stock vs local DB and fix discrepancies.
    Runs every 4h to catch phantom stock issues."""
    from app.services.ml_discrepancy_check import run_ml_discrepancy_check
    try:
        result = await run_ml_discrepancy_check()
        logger.info("ml_discrepancy_check_done", **result)
    except Exception as e:  # noqa: BLE001
        logger.error("ml_discrepancy_check_failed", error=str(e))


async def background_jobs_gc(ctx: dict) -> None:
    """Mark `running` jobs with no heartbeat in 5min as failed."""
    cutoff = datetime.now(UTC) - timedelta(minutes=5)
    async with session_scope() as s:
        result = await s.execute(
            update(BackgroundJob)
            .where(
                BackgroundJob.status == BackgroundJobStatus.RUNNING,
                or_(
                    BackgroundJob.last_heartbeat_at.is_(None),
                    BackgroundJob.last_heartbeat_at < cutoff,
                ),
            )
            .values(
                status=BackgroundJobStatus.FAILED,
                error="orphan_no_heartbeat",
                finished_at=datetime.now(UTC),
            )
        )
        logger.info("background_jobs_gc_done", marked_failed=result.rowcount or 0)


def _next_month_partition_bounds(now: datetime) -> tuple[str, str, str]:
    """Returns (partition_name, start_iso_date, end_iso_date) for month N+1
    relative to `now` (UTC)."""
    first = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc)
    nxt = (first + timedelta(days=32)).replace(day=1)
    end = (nxt + timedelta(days=32)).replace(day=1)
    name = f"sync_logs_y{nxt.year:04d}m{nxt.month:02d}"
    return name, nxt.date().isoformat(), end.date().isoformat()


async def sync_logs_partition_gc(ctx: dict) -> None:
    """Idempotently ensure next month's `sync_logs` partition exists."""
    name, start, end = _next_month_partition_bounds(datetime.now(UTC))
    schema = _settings.database_schema
    async with session_scope() as s:
        await s.execute(
            text(
                f'CREATE TABLE IF NOT EXISTS "{schema}".{name} '
                f'PARTITION OF "{schema}".sync_logs '
                f"FOR VALUES FROM ('{start}') TO ('{end}')"
            )
        )
    logger.info("sync_logs_partition_ensured", name=name, start=start, end=end)


async def alerts_cleanup(ctx: dict) -> None:
    """Delete alerts older than 60 days (B10)."""
    cutoff = datetime.now(UTC) - timedelta(days=60)
    async with session_scope() as s:
        result = await s.execute(delete(Alert).where(Alert.created_at < cutoff))
        logger.info("alerts_cleanup_done", deleted=result.rowcount or 0)


async def verificar_margem_snapshot(ctx: dict) -> None:
    """Snapshot novos itens de vw_conciliacao_margens_marketplace em
    davinci.verificar_margem. Skip rows already inserted via PK conflict on
    bling_order_item_id."""
    async with session_scope() as s:
        result = await s.execute(
            text(
                "INSERT INTO davinci.verificar_margem "
                "SELECT * FROM davinci.vw_conciliacao_margens_marketplace "
                "ON CONFLICT (bling_order_item_id) DO NOTHING"
            )
        )
        logger.info("verificar_margem_snapshot_done", inserted=result.rowcount or 0)


async def sync_lock_safety_release(ctx: dict) -> None:
    """SSH-parity: terminate backends idle >30min holding our SYNC_NAMESPACE
    advisory lock. Counterpart to SSH's in-memory 30-min safety timeout.

    Scenario this protects against: a sync_all/auto_link script is killed
    mid-flight (SIGKILL, OOM, a stopped TaskStop) — the asyncpg connection
    returns to the pool 'idle' but session-level advisory locks survive,
    blocking every future sync_all with sync_already_running until the
    connection is finally recycled (can be hours).
    """
    async with session_scope() as s:
        killed = await release_stale_sync_locks(s, idle_minutes=30)
        if killed:
            logger.warning("sync_lock_safety_release", killed=killed)
        else:
            logger.debug("sync_lock_safety_release_noop")


async def check_marketplace_shipped_orders(ctx: dict) -> None:
    """Sweeps bling_orders rows stuck in custom 'Em aberto' (situacao=83965)
    against Shopee/ML/Amazon shipment status. When a marketplace reports
    SHIPPED/shipped/Shipped, we bump Bling to situacao=15 ('Em andamento')
    via PATCH, then stamp em_andamento_data locally so the order surfaces
    in /controle-estoque's Pedidos and Envios tabs.

    Bling itself never auto-promotes 83965 → 15; carrier scans only update
    state on the marketplace side. This cron closes that gap every 5 min.
    See app.services.marketplace_shipment_check for the full strategy.
    """
    try:
        summary = await run_check_marketplace_shipped_orders()
        if summary.get("shipped_found") or summary.get("errors"):
            logger.info("shipment_check_cron_done", **summary)
        else:
            logger.debug("shipment_check_cron_noop", **summary)
    except Exception:  # noqa: BLE001
        logger.exception("shipment_check_cron_unhandled")


async def failed_jobs_alert_scan(ctx: dict) -> None:
    """Emit `sync_failure` alert per BackgroundJob that finished failed in
    the last 10 minutes. Dedupe key per job id keeps it idempotent across
    runs. Telegrams the user when `user_settings.notify_telegram` is on."""
    cutoff = datetime.now(UTC) - timedelta(minutes=10)
    async with session_scope() as s:
        rows = (
            await s.execute(
                select(BackgroundJob).where(
                    BackgroundJob.status == BackgroundJobStatus.FAILED,
                    BackgroundJob.finished_at.is_not(None),
                    BackgroundJob.finished_at >= cutoff,
                )
            )
        ).scalars().all()
        emitted = 0
        for job in rows:
            dedupe = f"sync_failure:job:{job.id}"
            title = f"Sync falhou: {job.type.value}"
            err = job.error or "unknown"
            payload = job.payload or {}
            trigger = payload.get("trigger") or payload.get("event") or job.type.value
            msg = (
                f"Job {job.type.value} terminou em failed após retries. "
                f"Trigger: {trigger}. Erro: {err}."
            )
            a = await emit_alert(
                s,
                user_id=job.created_by,
                type=AlertType.SYNC_FAILURE,
                severity=AlertSeverity.ERROR,
                title=title,
                message=msg,
                payload={
                    "job_id": str(job.id),
                    "job_type": job.type.value,
                    "error": err,
                    "trigger": trigger,
                    "delivery_id": payload.get("delivery_id"),
                    "product_id": payload.get("product_id"),
                },
                dedupe_key=dedupe,
                notify_telegram=False,
            )
            if a is None:
                continue
            emitted += 1
            us = await s.get(UserSettings, job.created_by)
            if us is not None and us.notify_telegram:
                from app.services.telegram import TelegramClient
                tg = TelegramClient()
                await tg.safe_send(
                    f"<b>DaVinci — Sync failure</b>\n{msg}",
                    chat_id=us.telegram_chat_id,
                )
        logger.info(
            "failed_jobs_alert_scan_done", scanned=len(rows), emitted=emitted
        )


WEBHOOK_SIG_FAIL_ALERT_THRESHOLD = 10


async def webhook_signature_alert_scan(ctx: dict) -> None:
    """If webhook signature failures pile up, the inline stock-update path is
    silently broken (wrong header alias, rotated secret, etc). Read the
    rolling 1h Redis counter populated by the webhook router; alert+telegram
    once per hour while the counter stays elevated."""
    import json as _json
    try:
        raw = await redis.get("webhook:bling:sig_fail_count")
        snap_raw = await redis.get("webhook:bling:sig_fail_last")
    except Exception as e:  # noqa: BLE001
        logger.warning("webhook_sig_alert_redis_failed", err=str(e))
        return
    count = int(raw) if raw else 0
    if count < WEBHOOK_SIG_FAIL_ALERT_THRESHOLD:
        return

    snap: dict = {}
    if snap_raw:
        try:
            snap = _json.loads(snap_raw) or {}
        except Exception:  # noqa: BLE001
            snap = {}

    async with session_scope() as s:
        from app.models import Integration as _Integ
        owner = (
            await s.execute(
                select(_Integ.user_id).where(
                    _Integ.platform == IntegrationPlatform.BLING
                ).limit(1)
            )
        ).scalar_one_or_none()
        if owner is None:
            return
        hour_bucket = datetime.now(UTC).strftime("%Y%m%d%H")
        msg = (
            f"{count} webhooks Bling rejeitados por assinatura na última hora. "
            f"Estoque pode estar desatualizado. Conferir BLING_WEBHOOK_SECRET e header alias."
        )
        diag_lines: list[str] = []
        if snap:
            diag_lines.append(f"reason: {snap.get('reason', '?')}")
            diag_lines.append(f"secret_len: {snap.get('secret_len', '?')}")
            diag_lines.append(f"body_len: {snap.get('body_len', '?')}")
            if snap.get("received_prefix") or snap.get("expected_prefix"):
                diag_lines.append(
                    f"hmac: recv={snap.get('received_prefix', '?')} "
                    f"vs exp={snap.get('expected_prefix', '?')}"
                )
            bh = snap.get("bling_headers") or {}
            if isinstance(bh, dict) and bh:
                diag_lines.append("headers: " + ", ".join(sorted(bh.keys())))
            else:
                diag_lines.append("headers: (none x-bling-*)")
        a = await emit_alert(
            s,
            user_id=owner,
            type=AlertType.GENERIC,
            severity=AlertSeverity.ERROR,
            title="Webhook Bling: assinatura inválida",
            message=msg,
            payload={"sig_fail_count": count, "hour_bucket": hour_bucket},
            dedupe_key=f"webhook_bling_sig_fail:{hour_bucket}",
            notify_telegram=False,
        )
        if a is None:
            return
        us = await s.get(UserSettings, owner)
        if us is not None and us.notify_telegram:
            from app.services.telegram import TelegramClient
            tg = TelegramClient()
            tg_msg = f"<b>DaVinci — Webhook Bling</b>\n{msg}"
            if diag_lines:
                tg_msg += "\n\n<b>Diag última falha:</b>\n<pre>" + "\n".join(diag_lines) + "</pre>"
            await tg.safe_send(tg_msg, chat_id=us.telegram_chat_id)


async def low_stock_polling(ctx: dict) -> None:
    """Emit one alert per product whose `stock < min_stock` (min_stock>0).
    Dedupe key collapses repeats inside the same UTC day."""
    today = datetime.now(UTC).date().isoformat()
    async with session_scope() as s:
        rows = (
            await s.execute(
                select(Product).where(
                    Product.min_stock > 0,
                    Product.stock < Product.min_stock,
                )
            )
        ).scalars().all()
        emitted = 0
        for p in rows:
            a = await emit_alert(
                s,
                user_id=p.user_id,
                type=AlertType.LOW_STOCK,
                severity=AlertSeverity.WARNING,
                title=f"Estoque baixo: {p.sku}",
                message=(
                    f"{p.name} — estoque {p.stock} abaixo do mínimo {p.min_stock}."
                ),
                payload={
                    "product_id": str(p.id),
                    "sku": p.sku,
                    "stock": p.stock,
                    "min_stock": p.min_stock,
                },
                dedupe_key=f"low_stock:{p.id}:{today}",
            )
            if a is not None:
                emitted += 1
        logger.info("low_stock_polling_done", scanned=len(rows), emitted=emitted)


async def auto_import_link(ctx: dict) -> None:
    """Fase 8: scan listings whose product_id is null and a non-blank SKU
    matches a product; attach product_id and promote into product_links.

    Cron tick (untracked) + hook safety-net. Manual UI trigger uses the
    `auto_import_link_run` variant below, which writes a BackgroundJob row
    so the operator can see progress in /sincronizacoes."""
    async with session_scope() as s:
        report = await run_auto_import_link(s)
        logger.info("auto_import_link_done", **report)


async def auto_import_link_run(ctx: dict, job_id: str) -> None:
    """Job-tracked variant of `auto_import_link` for the UI trigger."""
    async with session_scope() as s:
        job = await s.get(BackgroundJob, UUID(job_id))
        if job is None:
            logger.error("auto_import_link_run_job_missing", job_id=job_id)
            return
        job.status = BackgroundJobStatus.RUNNING
        job.started_at = datetime.now(UTC)
        job.last_heartbeat_at = job.started_at
        await s.commit()
        try:
            report = await run_auto_import_link(s)
            job.status = BackgroundJobStatus.SUCCEEDED
            job.result = report
        except Exception as e:  # noqa: BLE001
            logger.exception("auto_import_link_run_failed", job_id=job_id)
            job.status = BackgroundJobStatus.FAILED
            job.error = f"{type(e).__name__}: {e}"[:1000]
        finally:
            job.finished_at = datetime.now(UTC)
            await s.commit()


async def user_relink_run(ctx: dict, user_id: str) -> None:
    """Global relink + product_link promotion. Single-tenant CRM —
    user_id is kept for compatibility but the pass is global.
    Idempotent — re-running is a no-op."""
    async with session_scope() as s:
        linked = await _link_by_sku(s)
        promoted = await _create_product_links_for_matched(s)
        await s.commit()
        logger.info(
            "user_relink_done",
            user_id=user_id,
            linked=linked,
            product_links=promoted,
        )


async def import_listings_run(
    ctx: dict,
    job_id: str,
    user_id: str,
    integration_id: str,
    max_pages: int | None = None,
) -> None:
    """Fase 8: pull listings from a marketplace integration into local cache."""
    async with session_scope() as s:
        await run_import_listings(
            s,
            job_id=UUID(job_id),
            user_id=UUID(user_id),
            integration_id=UUID(integration_id),
            max_pages=max_pages,
        )


async def push_prices_batch_run(
    ctx: dict,
    job_id: str,
    user_id: str,
    items: list[dict],
    idempotency_prefix: str | None = None,
    notify_telegram: bool = True,
) -> None:
    """Fase 9c: bulk push de preços (sequencial, respeita rate-limit do client)."""
    async with session_scope() as s:
        await run_push_prices_batch(
            s,
            job_id=UUID(job_id),
            user_id=UUID(user_id),
            items=items,
            idempotency_prefix=idempotency_prefix,
            notify_telegram=notify_telegram,
        )


async def sync_bling_costs_run(
    ctx: dict,
    job_id: str,
    user_id: str,
) -> None:
    """Fase 9d: pulls precoCusto from Bling /produtos/{id} into pricing_products."""
    async with session_scope() as s:
        await run_sync_bling_costs(
            s, job_id=UUID(job_id), user_id=UUID(user_id)
        )


async def audit_run(
    ctx: dict,
    job_id: str,
    run_id: str,
    user_id: str,
) -> None:
    """Fase 10: audit by spreadsheet — compares planilha vs expected price."""
    async with session_scope() as s:
        await run_audit(
            s,
            job_id=UUID(job_id),
            run_id=UUID(run_id),
            user_id=UUID(user_id),
        )


async def bling_orders_safety_net_tick(ctx: dict) -> None:
    """A cada 10 min: pega até 30 pedidos suspeitos de stale (situacao
    6/83965 sem em_andamento_data, criados ≤14d, sem update há >15min) e
    força refetch via ingest_bling_order_run. Captura webhooks do Bling
    perdidos. Complementa (não substitui) check_marketplace_shipped_orders.
    Desligável via ENABLE_BLING_ORDERS_SAFETY_NET=false."""
    if not get_settings().enable_bling_orders_safety_net:
        return

    from app.services.bling_orders_safety_net import find_stale_order_ids

    async with session_scope() as session:
        candidates = await find_stale_order_ids(session)

    if not candidates:
        logger.info("bling_orders_safety_net_no_candidates")
        return

    pool = await get_arq_pool()
    enqueued = 0
    for bling_id, user_id in candidates:
        try:
            await pool.enqueue_job(
                "ingest_bling_order_run",
                bling_id,
                str(user_id),
                "safety_net",
            )
            enqueued += 1
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "bling_safety_net_enqueue_failed",
                bling_id=bling_id, err=str(e)[:200],
            )

    logger.info(
        "bling_orders_safety_net_tick_done",
        candidates=len(candidates), enqueued=enqueued,
    )


async def bling_orders_period_sync_tick(ctx: dict) -> None:
    """De hora em hora: lista os pedidos do Bling alterados nas últimas 2h
    (pela dataAlteracao) e re-ingere cada um via ingest_bling_order_run.
    Situação-agnóstico → recupera QUALQUER webhook perdido, inclusive
    transições que a safety-net não cobre (ex.: 15 → devolução 83957).
    Desligável via ENABLE_BLING_ORDERS_PERIOD_SYNC=false."""
    if not get_settings().enable_bling_orders_period_sync:
        return

    from app.services.bling_orders_period_sync import (
        find_recently_changed_bling_ids,
    )

    async with session_scope() as session:
        bling_ids, user_id = await find_recently_changed_bling_ids(session)

    if not bling_ids or user_id is None:
        logger.info("bling_orders_period_sync_no_candidates")
        return

    pool = await get_arq_pool()
    enqueued = 0
    for bling_id in bling_ids:
        try:
            await pool.enqueue_job(
                "ingest_bling_order_run",
                bling_id,
                str(user_id),
                "period_sync",
            )
            enqueued += 1
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "bling_period_sync_enqueue_failed",
                bling_id=bling_id, err=str(e)[:200],
            )

    logger.info(
        "bling_orders_period_sync_tick_done",
        candidates=len(bling_ids), enqueued=enqueued,
    )


# ---------------------------------------------------------------- lifecycle


async def startup(ctx: dict) -> None:
    from app.services.sentry import init_sentry
    init_sentry(component="worker")
    logger.info("worker_startup")


async def shutdown(ctx: dict) -> None:
    logger.info("worker_shutdown")


_FIVE_MIN = {0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55}
_TWO_MIN = set(range(0, 60, 2))
_TEN_MIN = {0, 10, 20, 30, 40, 50}


class WorkerSettings:
    redis_settings = RedisSettings.from_dsn(_settings.arq_redis_url)
    functions = [
        send_otp_email,
        auth_codes_cleanup,
        auto_link_run,
        sync_all_run,
        sync_product_run,
        ml_backfill_run,
        refresh_bling_stock_run,
        ingest_bling_order_run,
        auto_create_product_from_bling_run,
        create_bling_kit_for_mark_job,
        sync_import_product_to_bling_job,
        alerts_cleanup,
        low_stock_polling,
        import_listings_run,
        auto_import_link,
        auto_import_link_run,
        push_prices_batch_run,
        sync_bling_costs_run,
        audit_run,
        user_relink_run,
        sync_marketplace_financials_for_order_run,
        verificar_margem_snapshot,
        check_marketplace_shipped_orders,
        bling_orders_safety_net_tick,
        bling_orders_period_sync_tick,
    ]
    cron_jobs = [
        cron(auth_codes_cleanup, hour=6, minute=15, run_at_startup=False),
        # 06:00 UTC = 03:00 BRT — quiet window, also the daily-sync mass enqueue trigger.
        cron(daily_sync_scheduler, minute=_FIVE_MIN, run_at_startup=False),
        # Daily refresh de products.bling_cost_price (todos os produtos) via
        # listagem /produtos. 06:50 UTC = 03:50 BRT — janela tranquila, antes
        # do PDF de faturamento (05:00 BRT). Pedidos snapshotam esse custo.
        cron(product_bling_cost_sync, hour={6}, minute=50, run_at_startup=False),
        cron(bling_token_refresh, minute={15}, run_at_startup=False),
        cron(shopee_token_refresh, hour={0, 4, 8, 12, 16, 20}, minute=0, run_at_startup=False),
        cron(ml_token_refresh, minute={0, 30}, run_at_startup=False),
        cron(tiktok_token_refresh, hour={0, 6, 12, 18}, minute=45, run_at_startup=False),
        cron(marketplace_financials_retry, minute={10, 40}, run_at_startup=False),
        # Daily Frete refund sweep — 06:20 UTC = 03:20 BRT, in the quiet
        # window after the daily sync scheduler. Per-order hook in
        # marketplace_financials handles the realtime case; this cron
        # picks up ML freight diffs that settle days later.
        cron(refunds_freight_backfill, hour=6, minute=20, run_at_startup=False),
        cron(shopee_discrepancy_check, hour={1, 5, 9, 13, 17, 21}, minute=0, run_at_startup=False),
        cron(ml_discrepancy_check, hour={2, 6, 10, 14, 18, 22}, minute=30, run_at_startup=False),
        cron(background_jobs_gc, hour=6, minute=30, run_at_startup=False),  # 03:30 BRT
        cron(sync_logs_partition_gc, day=15, hour=3, minute=0, run_at_startup=False),
        # Stubs — registered so wiring later doesn't need a worker redeploy.
        cron(alerts_cleanup, hour=6, minute=0, run_at_startup=False),  # 03:00 BRT
        cron(failed_jobs_alert_scan, minute=_TWO_MIN, run_at_startup=False),
        cron(webhook_signature_alert_scan, minute={5, 35}, run_at_startup=False),
        cron(low_stock_polling, minute=_TWO_MIN, run_at_startup=False),
        # SSH parity: 30-min safety timeout for stuck sync advisory locks.
        # Runs every 5 minutes so the worst-case stuck duration is 35min.

        cron(sync_lock_safety_release, minute=_FIVE_MIN, run_at_startup=False),
        # Safety-net only — hooks via app.services.relink_hook handle the
        # day-to-day work. Runs at 02:00 and 14:00 UTC.
        cron(auto_import_link, hour={2, 14}, minute=0, run_at_startup=False),
        # Marketing module (per-platform/department) — every quarter-hour
        # per enabled MarketingAccount.
        cron(marketing_agent_cycle, minute={0, 15, 30, 45}, run_at_startup=False),
        # Marketing: pull live ML + Amazon ad data every half-hour at :05
        # and :35 (Shopee is NOT included — see marketing_shopee_tick).
        cron(marketing_full_sync, minute={5, 35}, run_at_startup=False),
        # Marketing: Shopee round-robin. One shop per tick, every 5 min.
        # 13 shops × 5min = each refreshes ~once per 65 minutes. The
        # per-partner Ads throttle can't sustain a full-batch sync; this
        # spreads the load thin enough to stay under it.
        cron(
            marketing_shopee_tick,
            minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55},
            run_at_startup=False,
        ),
        cron(verificar_margem_snapshot, minute={0, 30}, run_at_startup=False),
        # Sweeps bling_orders stuck in 'Em aberto' (83965) against the
        # marketplace's shipment state. Marketplace SHIPPED → Bling 15
        # + stamp em_andamento_data so the order shows on /controle-estoque.
        cron(check_marketplace_shipped_orders, minute=_FIVE_MIN, run_at_startup=False),
        # Safety-net: re-sincroniza pedidos suspeitos de stale (webhooks
        # perdidos do Bling) a cada 10 min. Refetch via ingest_bling_order_run.
        cron(bling_orders_safety_net_tick, minute=_TEN_MIN, run_at_startup=False),
        # Varredura por período: de hora em hora (:20) lista pedidos
        # alterados nas últimas 2h (dataAlteracao) e re-ingere. Pega webhooks
        # perdidos de QUALQUER transição (situação-agnóstica), inclusive as
        # que a safety-net não cobre (ex.: 15 → devolução).
        cron(bling_orders_period_sync_tick, minute={20}, run_at_startup=False),
    ]
    max_jobs = 10
    job_timeout = 1800
    keep_result = 3600
    max_tries = 3
    retry_jobs = True
    on_startup = startup
    on_shutdown = shutdown


# Re-export for tests / introspection
__all__ = [
    "WorkerSettings",
    "audit_run",
    "auth_codes_cleanup",
    "auto_import_link",
    "auto_link_run",
    "alerts_cleanup",
    "background_jobs_gc",
    "bling_orders_safety_net_tick",
    "bling_orders_period_sync_tick",
    "bling_token_refresh",
    "daily_sync_scheduler",
    "failed_jobs_alert_scan",
    "import_listings_run",
    "ingest_bling_order_run",
    "low_stock_polling",
    "product_bling_cost_sync",
    "ml_backfill_run",
    "ml_token_refresh",
    "marketplace_financials_retry",
    "refunds_freight_backfill",
    "refresh_bling_stock_run",
    "push_prices_batch_run",
    "sync_bling_costs_run",
    "shopee_discrepancy_check",
    "shopee_token_refresh",
    "send_otp_email",
    "sync_all_run",
    "sync_marketplace_financials_for_order_run",
    "webhook_signature_alert_scan",
    "sync_logs_partition_gc",
    "sync_product_run",
    "user_relink_run",
    "verificar_margem_snapshot",
    "_next_month_partition_bounds",
]
