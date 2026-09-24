import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, time, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import structlog
from arq import Retry, cron
from arq.connections import RedisSettings
from arq.worker import func
from sqlalchemy import and_, delete, or_, select, text, update
from sqlalchemy import func as sa_func  # `func` aqui já é o registrador de jobs do arq

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
    Fatura,
    Integration,
    IntegrationPlatform,
    Product,
    ProductLink,
    User,
    UserRole,
    UserSettings,
)
from app.redis_client import redis
from app.security.cipher import decrypt_json, encrypt_json
from app.services import (
    chamados_devolucao,
    chamados_devolucao_sync,
    chamados_pendencias,
    devolucao_mensagem_comprador,
    estoque_familia,
    threema,
)
from app.services.advisory_lock import release_stale_sync_locks, try_user_sync_lock
from app.services.alerts import emit_alert
from app.services.audit.runner import run_audit
from app.services.auto_link import run_auto_link
from app.services.bling_kit_create import create_bling_kit_for_mark_job
from app.services.bling_notas_token_refresh import run_refresh_bling_notas_tokens
from app.services.bling_orders import run_ingest_bling_order
from app.services.bling_product_create import run_auto_create_product_from_bling
from app.services.bling_situacoes_sync import sync_situacoes_bling
from app.services.chamados import run_replica_automatica as run_chamados_replica_automatica
from app.services.email import get_email_sender, render_otp_html
from app.services.import_lote_bling_stock import push_lote_stock_to_bling_job
from app.services.import_product_bling_create import sync_import_product_to_bling_job
from app.services.kit_components_sync import run_sync_kit_components
from app.services.listings_import import (
    _create_product_links_for_matched,
    _link_by_sku,
    run_auto_import_link,
    run_import_listings,
)
from app.services.logistica_ingest import (
    recarregar_ml,
    run_ingest_marketplaces_daily,
    run_ingest_ml_daily,
    sweeps_pos_venda,
)
from app.services.marketing import metricas as _metricas
from app.services.marketplace_financials import (
    run_due_marketplace_financial_retries,
    run_sync_marketplace_financials_for_bling_order,
)
from app.services.marketplace_shipment_check import run_check_marketplace_shipped_orders
from app.services.marketplaces.bling import BlingClient
from app.services.marketplaces.ml import MercadoLivreClient
from app.services.marketplaces.shopee import ShopeeClient
from app.services.ml_backfill import run_backfill_ml_stock
from app.services.nf_auto_enfileirar import run_auto_enfileirar_nf
from app.services.nf_recuperar import run_recuperar_nf
from app.services.notas_fiscais_export import run_export_notas
from app.services.ouvidoria import gc_ocorrencias as ouvidoria_gc_ocorrencias
from app.services.ouvidoria import gc_rodadas as ouvidoria_gc_rodadas
from app.services.ouvidoria import modo as ouvidoria_modo
from app.services.ouvidoria import sincronizar_catalogo as ouvidoria_sincronizar_catalogo
from app.services.pos_vendas import sync_notas_emitidas as run_pos_vendas_sync
from app.services.pricing.batch import run_push_prices_batch
from app.services.pricing.cost_sync import run_sync_bling_costs
from app.services.prioridade_estoque import prioridade_estoque_sweep
from app.services.prioridade_estoque_movimentos import manutencao_movimentos_sweep
from app.services.product_cost_sync import (
    run_restamp_order_costs,
    run_sync_import_bling_costs,
    run_sync_product_bling_costs,
)
from app.services.refresh_bling_stock import run_refresh_bling_stock
from app.services.refunds_freight_sync import backfill_freight_refunds
from app.services.sync_orchestrator import SyncOrchestrator
from app.services.valuation_estoque_snapshot import (
    repor_snapshot_se_faltar,
    run_valuation_estoque_snapshot,
)
from app.services.vigia_estoque_familia import vigia_estoque_familia_sweep
from app.services.vigia_importacao import vigia_importacao_sweep
from app.worker_pool import (
    ARQ_FINANCIALS_QUEUE,
    ARQ_MARKETPLACE_QUEUE,
    ARQ_SYNC_QUEUE,
    ARQ_UI_QUEUE,
    get_arq_pool,
    get_arq_sync_pool,
)

logger = structlog.get_logger()
_settings = get_settings()

# Brasília is UTC-3 (no DST since 2019). Cron schedules run in UTC: BRT = UTC-3.
SP_TZ = ZoneInfo("America/Sao_Paulo")

# Daily `sync_all_run` only processes products at risk of stockout. Items
# with `products.stock >= 10` are skipped — webhooks keep their stock fresh
# inline anyway, and high-stock items rarely cause real-world divergence.
# Hora em que a varredura diária roda para quem ligou e não escolheu hora.
DAILY_SYNC_HORA_PADRAO = time(3, 0)
SYNC_ALL_LOW_STOCK_THRESHOLD = 10


def _prefixos_familia() -> list[str]:
    """Linhas em que a soma por família está ligada, ou lista vazia.

    Vazio tem dois significados diferentes: a soma desligada (não isenta
    ninguém da varredura) e a soma ligada para TODAS as linhas (aí ninguém pode
    ser pulado, e é isso que o `not prefixos` trata em quem chama)."""
    s = get_settings()
    if not getattr(s, "estoque_familia_ativo", False):
        return []
    return [
        p.strip().lower()
        for p in (getattr(s, "estoque_familia_prefixos", "") or "").split(",")
        if p.strip()
    ]


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
    """Vincular Automático (massa). Adquire o mesmo advisory lock por usuário do
    `sync_all_run` pra que Sincronizar-Todos e Vincular sejam mutuamente
    exclusivos (exclusividade "só outro massa"). Não conseguiu o lock → marca o
    job FAILED com `sync_already_running`. O gate determinístico
    (`mass_sync_active`) no endpoint já recusa a maioria com 409; este lock é o
    backstop de runtime pra dois jobs que iniciem quase ao mesmo tempo."""
    jid = UUID(job_id)
    async with session_scope() as s:
        job = await s.get(BackgroundJob, jid)
        if job is None:
            logger.error("auto_link_run_job_missing", job_id=job_id)
            return
        async with try_user_sync_lock(s, job.created_by) as acquired:
            if not acquired:
                job.status = BackgroundJobStatus.FAILED
                job.error = "sync_already_running"
                job.finished_at = datetime.now(UTC)
                logger.warning("auto_link_run_locked", job_id=job_id)
                return
            await run_auto_link(
                s,
                job_id=jid,
                integration_ids=[UUID(i) for i in (integration_ids or [])] or None,
            )


async def sync_all_run(
    ctx: dict,
    job_id: str,
    user_id: str,
    product_ids: list[str] | None,
    include_all_stock: bool = False,
    integration_ids: list[str] | None = None,
    force: bool = False,
) -> None:
    """Fase 4a: full sync run. Acquires per-user advisory lock; if busy, marks
    job as `failed` with `error='sync_already_running'`.

    `include_all_stock=True` bypasses the cron-driven low-stock filter — used
    when the UI explicitly clicks "sync all" and the user expects every
    product to be pushed to its marketplaces.

    `force=True` makes the push bypass the marketplace-side safety guards (ML's
    B1 zero-guard + the paused/closed short-circuit), like the individual sync.
    Used when the operator explicitly wants the mass sync to re-push stock and
    reactivate listings ML auto-paused on stockout.
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
                # Estoque alto costuma ser mantido fresco pelos webhooks, por
                # isso a varredura pula esses. Mas com a soma por família o
                # número publicado depende do IRMÃO, e o webhook do irmão não
                # avisa este produto — então quem está numa linha com a soma
                # ligada entra na varredura mesmo com estoque alto.
                baixo = Product.stock < SYNC_ALL_LOW_STOCK_THRESHOLD
                prefixos_familia = _prefixos_familia()
                if prefixos_familia:
                    where.append(
                        or_(
                            baixo,
                            *[Product.sku.ilike(f"{p}%") for p in prefixos_familia],
                        )
                    )
                else:
                    where.append(baixo)
            stmt = select(Product)
            if where:
                stmt = stmt.where(and_(*where))
            products = (await s.execute(stmt)).scalars().all()
            pids = [p.id for p in products]
            # `integration_ids` scopes the push to the selected marketplace
            # accounts (mirrors the single-product sync's integration filter).
            # Narrow the product set to those that actually link to one of the
            # selected accounts, then include each such product's BLING link so
            # its stock refreshes BEFORE the marketplace push — otherwise a
            # scoped run would push whatever stale value sits in product.stock.
            only_link_ids: list[UUID] | None = None
            if integration_ids:
                iids = [UUID(i) for i in integration_ids]
                scoped_pids = (
                    (
                        await s.execute(
                            select(ProductLink.product_id)
                            .where(
                                and_(
                                    ProductLink.product_id.in_(pids),
                                    ProductLink.integration_id.in_(iids),
                                )
                            )
                            .distinct()
                        )
                    ).scalars().all()
                    if pids
                    else []
                )
                pids = list(scoped_pids)
                link_rows = (
                    (
                        await s.execute(
                            select(ProductLink.id).where(
                                and_(
                                    ProductLink.product_id.in_(pids),
                                    or_(
                                        ProductLink.integration_id.in_(iids),
                                        ProductLink.platform
                                        == IntegrationPlatform.BLING,
                                    ),
                                )
                            )
                        )
                    ).scalars().all()
                    if pids
                    else []
                )
                only_link_ids = list(link_rows)
            # Count links upfront so processed/total stays a real ratio
            # (the orchestrator bumps `processed` once per link). The
            # product count goes into payload so the UI can show both.
            if only_link_ids is not None:
                link_total = len(only_link_ids)
            elif pids:
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
            job.payload = {**(job.payload or {}), "total_products": len(pids)}
            await s.commit()

            # `force_bling_refresh=True`: daily sync_all also bypasses the
            # cached-refresh shortcut so every product gets fresh Bling stock
            # before pushing to marketplaces. (TTL is now 0 anyway, but the
            # flag stays explicit so the intent is clear if the constant
            # changes later.)
            orch = SyncOrchestrator(
                s, user_id=uid, job=job, force=force, force_bling_refresh=True
            )
            # SSH delta #1 — run_with_retry wraps run_parallel with up to
            # MAX_RESYNC_ROUNDS-1 retry passes for products whose links
            # came back RETRYABLE. The verify-before-send shortcut (delta
            # #2) and skipped_verified accounting are inside _process_link.
            report = await orch.run_with_retry(pids, only_link_ids=only_link_ids)

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

        # A soma por família publica o TOTAL de todos os lotes. Se a venda mexeu
        # só neste lote, o anúncio do irmão continuaria mostrando o total velho:
        # o webhook do Bling só avisa do produto que mudou, e a varredura diária
        # pula quem tem estoque próprio alto. Sem isto, dg053.ci ficaria preso no
        # número antigo até ele mesmo vender alguma coisa.
        if estoque_familia.familia_ligada(product.sku):
            nomes = [
                n
                for n in estoque_familia.irmaos(product.sku or "")
                if n != (product.sku or "").strip().lower()
            ]
            irmaos_prod = (
                (
                    await s.execute(
                        select(Product).where(
                            sa_func.lower(Product.sku).in_(nomes),
                            Product.situacao == "A",
                        )
                    )
                )
                .scalars()
                .all()
                if nomes
                else []
            )
            if irmaos_prod:
                logger.info(
                    "sync_product_irmaos_da_familia",
                    product_id=product_id,
                    sku=product.sku,
                    irmaos=[p.sku for p in irmaos_prod],
                )
                await orch.run(irmaos_prod)


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


async def export_notas_run(ctx: dict, job_id: str) -> None:
    """Export assíncrono de NF-e (xlsx/zip) pra lotes grandes — foge do
    timeout do proxy do export síncrono."""
    async with session_scope() as s:
        await run_export_notas(s, job_id=UUID(job_id))


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


# Deve casar com WorkerSettings.max_tries (fila default). Usado só para decidir
# quando marcar o BackgroundJob durável como FAILED (na última tentativa arq),
# sem depender de ctx["max_tries"] (não garantido em toda versão do arq).
INGEST_ORDER_MAX_TRIES = 3


async def _mark_ingest_job(job_id: UUID, **values: Any) -> None:
    """Grava status no BackgroundJob durável do ingest de pedido, em transação
    PRÓPRIA — sobrevive ao rollback da transação do ingest quando ele falha.
    Best-effort: nunca deixa a falha do UPDATE derrubar o job de ingest."""
    try:
        async with session_scope() as s:
            await s.execute(
                update(BackgroundJob)
                .where(BackgroundJob.id == job_id)
                .values(**values)
            )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "ingest_job_mark_failed", job_id=str(job_id), err=str(e)[:200]
        )


async def ingest_bling_order_run(
    ctx: dict,
    bling_order_id: int,
    user_id: str,
    event: str | None = None,
    job_id: str | None = None,
) -> None:
    """Fase 5b: ingest a Bling pedido de venda triggered by webhook.

    Per-order advisory lock keeps concurrent webhook deliveries serialized
    for the same order while still allowing parallelism across orders.

    Quando `job_id` é passado (caminho do webhook e do sweep), atualiza o
    BackgroundJob durável: RUNNING no início, SUCCEEDED no fim, e FAILED só na
    ÚLTIMA tentativa do arq — as intermediárias re-levantam pro arq re-tentar
    com backoff sem marcar FAILED (evita alerta falso). Esgotadas as tentativas,
    o registro fica FAILED (visível ao failed_jobs_alert_scan) e o
    ingest_orders_retry_sweep re-dirige. Chamadas sem `job_id` (redes de
    recuperação) mantêm o comportamento antigo: 3 tentativas e o próprio cron
    re-enfileira no próximo tick.
    """
    uid = UUID(user_id)
    jid = UUID(job_id) if job_id else None
    job_try = int(ctx.get("job_try", 1) or 1)
    if jid is not None:
        now = datetime.now(UTC)
        await _mark_ingest_job(
            jid,
            status=BackgroundJobStatus.RUNNING,
            started_at=now,
            last_heartbeat_at=now,
        )
    lock_key = f"ingest_bling_order:{bling_order_id}"
    try:
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
    except Exception as e:  # noqa: BLE001
        terminal = job_try >= INGEST_ORDER_MAX_TRIES
        logger.error(
            "ingest_bling_order_failed",
            bling_order_id=bling_order_id,
            bling_event=event,
            attempt=job_try,
            terminal=terminal,
            err=f"{type(e).__name__}: {str(e)[:500]}",
        )
        if not terminal:
            raise  # deixa o arq re-tentar (Bling 5xx / contenção de lock)
        if jid is not None:
            await _mark_ingest_job(
                jid,
                status=BackgroundJobStatus.FAILED,
                finished_at=datetime.now(UTC),
                error=f"{type(e).__name__}: {str(e)[:1000]}",
            )
        # Registro durável (se houver) já gravou a falha; o sweep re-dirige.
        # Engole pra não empilhar log de falha permanente do arq pro mesmo job.
        return
    if jid is not None:
        done = datetime.now(UTC)
        await _mark_ingest_job(
            jid,
            status=BackgroundJobStatus.SUCCEEDED,
            processed=1,
            finished_at=done,
            last_heartbeat_at=done,
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


async def marketing_consume_commands(ctx: dict) -> None:
    """Agent node only (~every 20s): drain the marketing_commands outbox —
    pause/resume/budget actions, manual or schedule-driven — against the
    live Shopee/ML Ads APIs. Registered ONLY when
    `settings.marketing_agent_node` is set, so the central server never
    executes ad actions. Body re-checks the flag defensively."""
    if not _settings.marketing_agent_node:
        return
    from app.services.marketing.commands import consume_pending_commands

    async with session_scope() as s:
        try:
            await consume_pending_commands(s)
        except Exception as e:  # noqa: BLE001
            logger.error("marketing_consume_commands_failed", err=str(e)[:300])


async def marketing_reconcile_schedules(ctx: dict) -> None:
    """CENTRAL server (~every 60s): compare each schedule-enabled account's
    desired BRT state against its actual state and enqueue a correcting command
    on drift. Pure DB work — it only writes to the marketing_commands outbox,
    never calls a marketplace API — so unlike the consumer (pinned to the agent
    node for the partner throttle) it's safe to run on the always-on central
    node. The enqueued commands are executed elsewhere: Shopee ('browser') by
    the LOCAL marionete via /agent/lease, ML/Amazon ('api') by the agent-node
    consumer. Gated by `enable_marketing` (no-op in prod until the module is
    switched on); reconverges after any restart."""
    if not _settings.enable_marketing:
        return
    from app.services.marketing.reconcile import reconcile_schedules

    async with session_scope() as s:
        try:
            r = await reconcile_schedules(s)
        except Exception as e:  # noqa: BLE001
            logger.error("marketing_reconcile_failed", err=str(e)[:300])
            return
    if r.get("enqueued"):
        logger.info("marketing_reconcile_tick", **r)


async def marketing_flash_duplicate(ctx: dict) -> None:
    """CENTRAL server (01:00 BRT = 04:00 UTC): enqueue the daily Oferta Relâmpago
    (Shopee flash-sale) duplication for every `flash_duplicate_enabled` account.
    Pure DB work — writes one 'flash_duplicate' command (executor='browser',
    payload {commit:true}) to the outbox per account; the LOCAL marionete leases
    and duplicates the running offer into the next day with openings. The
    commit=true only creates for real when the executor has SELECTORS_CALIBRATED
    =true, so this stays a safe no-op end-to-end until the operator validates.
    Gated by `enable_marketing` (no-op in prod until the module is switched on)."""
    if not _settings.enable_marketing:
        return
    from app.services.marketing.flash import enqueue_flash_duplicates

    async with session_scope() as s:
        try:
            r = await enqueue_flash_duplicates(s)
        except Exception as e:  # noqa: BLE001
            logger.error("marketing_flash_duplicate_failed", err=str(e)[:300])
            return
    logger.info("marketing_flash_duplicate_tick", **r)


async def marketing_postagens_promover(ctx: dict) -> None:
    """Robô de postagem (Eduardo, 15/09/2026) — a cada minuto: a agenda vira fila.

    `agendado_para <= agora` → `pendente`, que é o que o publicador do próximo
    tick enxerga. Agendamento atrasado demais (`marketing_postagem_atraso_max_
    min`, 6h por default — worker parado, deploy longo, servidor de volta na
    segunda) NÃO sai sozinho: vira `revisar` e um humano decide, porque "o post
    das 19h de sexta" publicado no domingo de manhã é pior que post nenhum.
    Mesmo cuidado do `valuation_estoque_catchup` com catch-up antigo.

    Só escreve no banco — não fala com a Meta — então mora aqui no worker
    central, como o `marketing_reconcile_schedules`. Gated por
    `enable_marketing` (no-op em prod até ligar o módulo)."""
    if not _settings.enable_marketing:
        return
    from app.services.marketing.postagens import promover_agendadas

    async with session_scope() as s:
        try:
            r = await promover_agendadas(s) or {}
        except Exception as e:  # noqa: BLE001
            logger.error("marketing_postagens_promover_failed", err=str(e)[:300])
            return
    if any(bool(v) for v in r.values()):
        logger.info("marketing_postagens_promover_tick", **r)


async def dm_responder_pendentes(ctx: dict) -> None:
    """A cada minuto: manda as respostas de DM enfileiradas.

    Cliente com o celular na mão espera resposta em segundos — resposta em 40
    minutos é pior que nenhuma. Por isso tick de 1 minuto, e o lease usa SKIP
    LOCKED, então dois ticks se sobrepondo é inofensivo: cada um reserva
    linhas diferentes.

    As travas ficam TODAS dentro do serviço (`dm_resposta_commit`, a allowlist
    de IGSIDs e o `dm_auto` por conta) — aqui não se decide nada, só se chama.
    Com o commit desligado, este cron percorre o caminho inteiro e grava
    `seco`, que é como se roda uma semana em produção lendo o que o robô TERIA
    dito, sem escrever pra cliente nenhum.
    """
    from app.services import instagram_dm as _dm

    async with session_scope() as s:
        try:
            tratadas = await _dm.responder_pendentes(s, limit=10)
        except Exception as e:  # noqa: BLE001
            logger.error("dm_responder_failed", err=str(e)[:300])
            return
    if tratadas:
        logger.info("dm_responder_tick", tratadas=tratadas)


async def marketing_postagens_publicar(ctx: dict) -> None:
    """A cada minuto: publica as postagens que já podem sair.

    `proximas_para_publicar` é quem decide QUEM sai (fila `pendente`, teto
    diário por conta, espaçamento de 90 min) e já deixa as linhas reservadas —
    aqui só se executa, na ordem que ela devolveu.

    A trava é `settings.marketing_postagem_commit` (False por default, mesmo
    espírito do SELECTORS_CALIBRATED do executor): sem ela o robô percorre
    TUDO — acha o arquivo no disco, resolve a conta, monta a legenda — e grava
    `DRY: publicaria em @conta` sem tocar na Meta. É assim que se testa local e
    em produção antes de soltar de verdade na conta da marca.

    Com commit ligado: decifra o token da conta (`cipher.decrypt_json`, que
    nunca é logado), chama o `meta_client` da plataforma e grava o resultado. O
    `container_id` é persistido pelo callback ANTES da chamada que publica —
    publicar não tem desfazer, então a reconciliação precisa ter por onde
    perguntar "será que saiu?" se este processo morrer no meio.

    Uma exceção inesperada NÃO derruba o tick: aquela linha fica reservada e o
    `marketing_postagens_reconciliar` a resolve (consultando, nunca
    republicando)."""
    if not _settings.enable_marketing:
        return
    from pathlib import Path

    from app.models.marketing import MarketingCreativeFile
    from app.models.marketing_postagem import (
        STATUS_FALHOU,
        STATUS_PUBLICADO,
        STATUS_REVISAR,
        MarketingPostagem,
        RedeSocialToken,
    )
    from app.services.marketing import link_criativo, meta_client, youtube_client
    from app.services.marketing import postagens as _postagens

    async with session_scope() as s:
        try:
            fila = await _postagens.proximas_para_publicar(s) or []
        except Exception as e:  # noqa: BLE001
            logger.error("marketing_postagens_publicar_failed", err=str(e)[:300])
            return
        # Cópia em valores simples ANTES do laço: se uma postagem estourar, o
        # `rollback` do except EXPIRA todas as linhas da sessão — ler
        # `postagem.id` da linha seguinte viraria lazy-load, proibido em sessão
        # async. Com o retrato em mãos, uma falha não contamina as outras.
        alvos = [
            {
                "id": p.id,
                "conta": p.conta or "conta sem @",
                "plataforma": p.plataforma,
                "file_id": p.file_id,
                "rede_social_id": p.rede_social_id,
                "legenda": p.legenda or "",
                "opcoes": p.opcoes or {},
            }
            for p in fila
        ]
        publicadas = secas = falhas = adiadas = 0
        # Orçamento do tick: o job tem 1200s no cron, e um Reel sozinho pode
        # levar 600s de upload + 300s de poll. Passado o teto, o que ainda NÃO
        # foi tocado volta pra fila — melhor o próximo tick pegar do que o arq
        # matar o processo no meio de uma publicação.
        comeco = datetime.now(UTC)
        orcamento = timedelta(seconds=600)
        for indice, alvo in enumerate(alvos):
            conta = alvo["conta"]
            if datetime.now(UTC) - comeco > orcamento:
                await _postagens.devolver_para_fila(
                    s,
                    [a["id"] for a in alvos[indice:]],
                    motivo="a fila do minuto encheu — volta no próximo ciclo",
                )
                break
            try:
                # AGENDAR NÃO É AUTORIZAR PRA SEMPRE. Entre o agendamento e
                # este instante o criativo pode ter sido reprovado, a conta
                # desativada, o `postagem_auto` desligado ou a conta pode ter
                # recebido outros posts. As guardas rodam de novo, agora.
                linha = await s.get(MarketingPostagem, alvo["id"])
                motivo = await _postagens.revalidar(s, linha) if linha else "postagem_sumiu"
                if motivo in _postagens.MOTIVOS_ADIAVEIS:
                    # Só ainda não é hora: volta pra fila sem gastar tentativa.
                    # É isto que segura a rajada de catch-up depois de o worker
                    # ficar parado — sem ele os posts atrasados sairiam todos
                    # colados na mesma conta.
                    await _postagens.adiar(s, linha, motivo=motivo)
                    adiadas += 1
                    continue
                if motivo:
                    await _postagens.registrar_resultado(
                        s,
                        alvo["id"],
                        status=STATUS_FALHOU,
                        result=f"não pôde publicar em {conta}: {motivo}",
                    )
                    falhas += 1
                    continue

                arquivo = await s.get(MarketingCreativeFile, alvo["file_id"])
                caminho = (
                    Path(_settings.uploads_dir) / arquivo.file_rel if arquivo else None
                )
                # Conferir o arquivo ANTES do modo seco: um "DRY ok" com o
                # vídeo sumido do disco seria um teste que mente.
                if caminho is None or not caminho.is_file():
                    await _postagens.registrar_resultado(
                        s,
                        alvo["id"],
                        status=STATUS_FALHOU,
                        result="arquivo do criativo não está no disco do servidor",
                    )
                    falhas += 1
                    continue

                if not _settings.marketing_postagem_commit:
                    await _postagens.registrar_resultado(
                        s,
                        alvo["id"],
                        status=STATUS_PUBLICADO,
                        result=(
                            f"DRY: publicaria em {conta} ({alvo['plataforma']}) — "
                            f"{arquivo.file_name}"
                        ),
                    )
                    secas += 1
                    continue

                tok = (
                    await s.execute(
                        select(RedeSocialToken).where(
                            RedeSocialToken.rede_social_id == alvo["rede_social_id"]
                        )
                    )
                ).scalars().first()
                if tok is None or not tok.token_enc or not tok.external_user_id:
                    await _postagens.registrar_resultado(
                        s,
                        alvo["id"],
                        status=STATUS_FALHOU,
                        result=f"{conta} não tem token conectado — reconecte a conta",
                    )
                    falhas += 1
                    continue
                try:
                    segredos = decrypt_json(tok.token_enc)
                    # O token da PÁGINA é o que a doc de Content Publishing
                    # pede ("A Page access token requested from your app user
                    # who can perform the CREATE_CONTENT task on the Page").
                    # Só existe na trilha do Facebook e é gravado no conectar;
                    # sem ele, cai no token colado — que costuma funcionar
                    # quando o usuário do sistema tem CREATE_CONTENT, e é o
                    # único que existe na trilha do Instagram.
                    access_token = (
                        segredos.get("page_access_token")
                        or segredos.get("access_token")
                        # YouTube guarda o REFRESH token (que não expira) e
                        # troca por um access de 1h na hora de publicar. Aqui
                        # a variável só precisa provar "existe credencial".
                        or segredos.get("refresh_token")
                        or ""
                    ).strip()
                except Exception:  # noqa: BLE001
                    # Nada do erro de cifra vai pro `result`: ele pode carregar
                    # pedaço do material cifrado.
                    access_token = ""
                if not access_token:
                    await _postagens.registrar_resultado(
                        s,
                        alvo["id"],
                        status=STATUS_FALHOU,
                        result=f"token de {conta} ilegível — reconecte a conta",
                    )
                    falhas += 1
                    continue

                async def _gravar_container(cid: str, _pid=alvo["id"], _s=s) -> None:
                    """Carimba o id externo assim que ele existe — antes de publicar.

                    UPDATE direto (e não a linha do ORM) porque este commit pode
                    acontecer depois de um rollback de outra postagem do mesmo
                    tick, com os objetos expirados."""
                    await _s.execute(
                        update(MarketingPostagem)
                        .where(MarketingPostagem.id == _pid)
                        .values(container_id=cid)
                    )
                    await _s.commit()

                if alvo["plataforma"] == meta_client.PLATAFORMA_FACEBOOK:
                    res = await meta_client.publicar_reel_facebook(
                        page_id=tok.external_user_id,
                        token=access_token,
                        video_path=caminho,
                        legenda=alvo["legenda"],
                        # A agenda é NOSSA: quando a linha chega aqui, a hora já
                        # passou. Usar o `SCHEDULED` nativo do Facebook faria a
                        # mesma postagem se comportar diferente do Instagram
                        # (que não tem agendamento na API) — e o operador
                        # perderia o botão de cancelar.
                        agendado_para=None,
                        ao_criar_container=_gravar_container,
                    )
                elif alvo["plataforma"] == meta_client.PLATAFORMA_INSTAGRAM:
                    # Sem Página do Facebook (caso das marcas hoje) a Meta
                    # BAIXA o vídeo: mandamos um link assinado que vale 15 min
                    # e é gerado agora, nunca guardado. Com Página, o setting
                    # vira "binario" e o arquivo sobe direto (nada exposto).
                    # A trilha Instagram Login (conta sem Página) NÃO tem
                    # upload binário: ali o link é obrigatório, independente do
                    # setting.
                    so_link = (
                        _settings.marketing_postagem_upload == "link"
                        or tok.provedor == meta_client.PROVEDOR_INSTAGRAM
                    )
                    link = link_criativo.url_video(alvo["file_id"]) if so_link else None
                    res = await meta_client.publicar_reel_instagram(
                        ig_user_id=tok.external_user_id,
                        token=access_token,
                        video_path=caminho,
                        legenda=alvo["legenda"],
                        share_to_feed=bool(alvo["opcoes"].get("share_to_feed", True)),
                        video_url=link,
                        # Quem escolhe o host da Graph é a ORIGEM DO TOKEN.
                        provedor=tok.provedor,
                        ao_criar_container=_gravar_container,
                    )
                elif alvo["plataforma"] == youtube_client.PLATAFORMA_YOUTUBE:
                    # Sem container: o upload resumável devolve o id do vídeo
                    # na mesma chamada que envia os bytes. O que a gente
                    # carimba é a SESSÃO de upload, antes de subir — se o
                    # processo morrer no meio, é o registro que impede alguém
                    # de republicar às cegas e duplicar vídeo no canal.
                    res = await youtube_client.publicar_video_youtube(
                        refresh_token=access_token,
                        video_path=caminho,
                        legenda=alvo["legenda"],
                        ao_abrir_sessao=_gravar_container,
                    )
                else:
                    await _postagens.registrar_resultado(
                        s,
                        alvo["id"],
                        status=STATUS_FALHOU,
                        result=f"plataforma ainda não suportada: {alvo['plataforma']}",
                    )
                    falhas += 1
                    continue

                # Saúde da credencial na própria linha do token — é o que a tela
                # de Redes Sociais mostra sem nunca decifrar nada.
                if res.ok:
                    tok.last_ok_at = datetime.now(UTC)
                else:
                    tok.last_error = (res.erro or "")[:500] or None
                if res.ok:
                    status_final, texto = STATUS_PUBLICADO, f"publicado em {conta}"
                elif res.ambiguo:
                    # A chamada que publica SAIU e a resposta se perdeu. Chamar
                    # isso de "falhou" libera o retry — e o retry publica o
                    # segundo Reel. Fica em `revisar`: o reconciliador pergunta
                    # à Meta pelo container antes de qualquer coisa.
                    status_final = STATUS_REVISAR
                    texto = (
                        f"a Meta não respondeu depois do passo que publica em {conta}: "
                        f"{res.erro or 'sem resposta'}. NÃO republique — confira a conta "
                        "(o reconciliador consulta sozinho no próximo ciclo)."
                    )
                else:
                    status_final, texto = STATUS_FALHOU, (res.erro or "falhou sem motivo")
                await _postagens.registrar_resultado(
                    s,
                    alvo["id"],
                    status=status_final,
                    result=texto[:2000],
                    post_external_id=res.post_external_id,
                    post_url=res.post_url,
                    container_id=res.container_id,
                )
                publicadas += 1 if res.ok else 0
                falhas += 0 if res.ok else 1
            except Exception as e:  # noqa: BLE001
                # A linha fica reservada de propósito: quem resolve é o
                # reconciliador, CONSULTANDO — retentar cego duplicaria o Reel.
                await s.rollback()
                logger.error(
                    "marketing_postagens_publicar_item_failed",
                    postagem=str(alvo["id"]),
                    err=str(e)[:300],
                )
    if publicadas or secas or falhas or adiadas:
        logger.info(
            "marketing_postagens_publicar_tick",
            publicadas=publicadas,
            secas=secas,
            falhas=falhas,
            adiadas=adiadas,
        )


async def marketing_autopostagem(ctx: dict) -> None:
    """O robô escolhe o vídeo e a hora sozinho (Eduardo, 24/09/2026).

    De hora em hora, no minuto 2 (longe do congestionamento do :00). Só toca
    em conta que tem o interruptor ligado E a hora de início preenchida — o
    interruptor sozinho significava outra coisa até hoje, e há conta ligada por
    esse motivo.

    Ele AGENDA; quem publica é o `marketing_postagens_publicar`, que já roda a
    cada minuto. Duas cópias da lógica de publicar foi o que quebrou o modal do
    TikTok em 23/09 — não repito o erro.

    De hora em hora e não a cada minuto porque a grade é horária: rodar mais
    vezes não adianta nada, e cada passada varre criativos de todas as marcas.
    """
    if not _settings.enable_marketing:
        return
    from app.services.marketing.autopostagem import rodada

    async with session_scope() as s:
        try:
            r = await rodada(s)
        except Exception as e:  # noqa: BLE001
            logger.error("marketing_autopostagem_failed", err=str(e)[:300])
            return
    if r.get("agendadas"):
        logger.info("marketing_autopostagem_tick", **r)


async def _rodar_metricas(modo: str) -> None:
    """Uma rodada da leitura dos vídeos publicados, com uma rodada por vez.

    A trava no Redis existe porque agora são três portas pra mesma leitura (a
    noite, o passe de hora em hora e o botão da tela) e duas rodadas juntas
    leriam o mesmo post do TikTok duas vezes do mesmo IP. Travado: o passe de
    hora em hora e o botão simplesmente não rodam (o próximo resolve); a da
    noite tenta de novo em 5 min, porque é ela que fecha o retrato do dia.

    Sem Redis, roda sem trava (falha aberta): leitura dobrada é menos ruim
    que noite sem leitura. No fim grava a `ultima_rodada`, que é como a tela
    sabe que o "Atualizar agora" terminou.
    """
    if not _settings.enable_marketing:
        return
    from app.services.marketing.metricas import coletar

    travou = False
    try:
        travou = bool(await redis.set(_metricas.CHAVE_RODANDO, modo, nx=True, ex=1800))
        if not travou:
            if modo == "completo":
                raise Retry(defer=300)
            logger.info("marketing_metricas_pulada", modo=modo, motivo="outra_rodada_em_andamento")
            return
    except Retry:
        raise
    except Exception as e:  # noqa: BLE001 — Redis fora: roda sem trava
        logger.warning("marketing_metricas_trava_indisponivel", modo=modo, err=str(e)[:200])

    inicio = datetime.now(UTC)
    r: dict[str, int] = {"total": 0, "ok": 0, "falhou": 0}
    # A da noite grava no dia DA NOITE mesmo se começou depois da meia-noite
    # (fila atrasada, ou o Retry de 5 min): é o retrato do fim daquele dia.
    dia = _metricas.dia_da_noite(inicio) if modo == "completo" else None
    try:
        async with session_scope() as s:
            try:
                r = await coletar(s, modo=modo, dia=dia)
            except Exception as e:  # noqa: BLE001
                logger.error("marketing_metricas_failed", modo=modo, err=str(e)[:300])
    finally:
        try:
            if travou:
                await redis.delete(_metricas.CHAVE_RODANDO)
            await redis.set(
                _metricas.CHAVE_ULTIMA_RODADA,
                json.dumps(
                    {
                        "modo": modo,
                        "inicio": inicio.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                        "fim": datetime.now(UTC)
                        .isoformat(timespec="milliseconds")
                        .replace("+00:00", "Z"),
                        **r,
                    }
                ),
                ex=8 * 86400,
            )
        except Exception as e:  # noqa: BLE001 — o TTL solta a trava sozinho
            logger.warning("marketing_metricas_fim_sem_redis", modo=modo, err=str(e)[:200])
    logger.info("marketing_metricas_tick", modo=modo, **r)


async def marketing_postagens_metricas(ctx: dict) -> None:
    """Quanto cada vídeo publicado rendeu (Eduardo, 23/09/2026) — a leitura da noite.

    Um RETRATO por dia por publicação. As três redes devolvem número acumulado
    ("este vídeo tem 400 views"), nunca o do dia; é a diferença entre dois
    retratos que responde "quanto rendeu esta semana".

    Às 23:47 BRT (02:47 UTC) desde 24/09/2026 — era 04:20. Às 23:47 o dia D
    quase fechou, então o retrato de D é o número do FIM de D, e "views
    ganhas por dia" cai no dia certo. O das 04:20 de D era, na prática, o fim
    de D-1: o ganho de cada dia aparecia no dia seguinte. E a comparação "na
    mesma idade" (D+1, D+3, D+7) precisa de uma leitura perto do fim de cada
    dia de vida do vídeo. Lê TUDO, o mais atrasado primeiro.
    """
    await _rodar_metricas("completo")


async def marketing_postagens_metricas_recentes(ctx: dict) -> None:
    """De hora em hora (menos na hora da noite): o vídeo novo e a falha recente.

    Eduardo, 24/09/2026: "esses vídeos ainda não aparecem… será que demora?".
    Demorava até 16 h — o vídeo só ganhava número na leitura da madrugada.
    Este passe lê o que NUNCA foi lido (com 20 min de publicado), e tenta de
    novo o que falhou nos últimos 14 dias. Não relê o resto: número acumulado
    lido de hora em hora não traz informação nova, só gasta cota e IP.
    """
    await _rodar_metricas("recentes")


async def marketing_postagens_metricas_agora(ctx: dict) -> None:
    """O botão "Atualizar agora" da tela (fila UI, não cron).

    Lê o nunca lido e a última semana que não foi lida nos últimos 10 min. A
    trava de 10 min do botão fica no router; aqui é a mesma rodada de sempre.
    """
    await _rodar_metricas("agora")


async def marketing_postagens_reconciliar(ctx: dict) -> None:
    """A cada 10 min: postagem presa em `containering`/`publicando` há > 15 min.

    Publicar NÃO é idempotente: um timeout DEPOIS que a Meta aceitou é
    indistinguível de uma falha antes dela. Então aqui não existe retry — o
    robô PERGUNTA pelo `container_id`/`post_external_id` que ficou gravado:

      • saiu     → `publicado`, com o link (e ninguém republica);
      • não saiu → `revisar`, que é um humano decidindo se refaz.

    Em modo seco nem pergunta: sem commit nenhuma chamada à Meta aconteceu, uma
    linha presa só pode ser worker morto no meio — vai direto pra `revisar`."""
    if not _settings.enable_marketing:
        return
    from app.models.marketing_postagem import (
        STATUS_CONTAINERING,
        STATUS_PUBLICADO,
        STATUS_PUBLICANDO,
        STATUS_REVISAR,
        MarketingPostagem,
        RedeSocialToken,
    )
    from app.services.marketing import meta_client
    from app.services.marketing import postagens as _postagens

    limite = datetime.now(UTC) - timedelta(minutes=15)
    # Depois de 48h insistindo, a Meta não vai mudar de ideia: a linha fica em
    # `revisar` pro operador e o cron para de perguntar.
    idade_max = datetime.now(UTC) - timedelta(hours=48)
    async with session_scope() as s:
        presas = (
            await s.execute(
                select(MarketingPostagem)
                .where(
                    or_(
                        and_(
                            MarketingPostagem.status.in_(
                                (STATUS_CONTAINERING, STATUS_PUBLICANDO)
                            ),
                            # `claimed_at` é o carimbo de quando o publicador
                            # pegou a linha; sem ele (estado escrito por outro
                            # caminho) vale o `updated_at`.
                            or_(
                                MarketingPostagem.claimed_at <= limite,
                                and_(
                                    MarketingPostagem.claimed_at.is_(None),
                                    MarketingPostagem.updated_at <= limite,
                                ),
                            ),
                        ),
                        # A DÚVIDA também volta aqui: o publicador manda pra
                        # `revisar` quando a chamada que publica não respondeu.
                        # Essa linha tem container e não tem link — é
                        # exatamente a pergunta que este cron sabe fazer, e
                        # deixá-la parada seria empurrar pro operador uma
                        # resposta que a Meta dá de graça.
                        and_(
                            MarketingPostagem.status == STATUS_REVISAR,
                            MarketingPostagem.container_id.isnot(None),
                            MarketingPostagem.post_url.is_(None),
                            MarketingPostagem.created_at >= idade_max,
                        ),
                    )
                )
                .order_by(MarketingPostagem.claimed_at.asc())
                .limit(50)
            )
        ).scalars().all()
        if not presas:
            return
        # Mesmo retrato em valores simples do publicador: o rollback de uma
        # linha expira as outras da sessão.
        alvos = [
            {
                "id": p.id,
                "plataforma": p.plataforma,
                "container_id": p.container_id,
                "post_external_id": p.post_external_id,
                "post_url": p.post_url,
                "rede_social_id": p.rede_social_id,
            }
            for p in presas
        ]
        resolvidas = revisar = 0
        for alvo in alvos:
            ident = alvo["post_external_id"] or alvo["container_id"]
            try:
                token = ""
                provedor = meta_client.PROVEDOR_FACEBOOK
                if _settings.marketing_postagem_commit and ident:
                    tok = (
                        await s.execute(
                            select(RedeSocialToken).where(
                                RedeSocialToken.rede_social_id == alvo["rede_social_id"]
                            )
                        )
                    ).scalars().first()
                    if tok is not None and tok.token_enc:
                        provedor = tok.provedor
                        try:
                            token = (
                                decrypt_json(tok.token_enc).get("access_token") or ""
                            ).strip()
                        except Exception:  # noqa: BLE001
                            token = ""
                if not token:
                    await _postagens.registrar_resultado(
                        s,
                        alvo["id"],
                        status=STATUS_REVISAR,
                        result=(
                            "presa sem como consultar a Meta "
                            f"(modo seco ou conta sem token) — id externo: {ident or '—'}"
                        ),
                    )
                    revisar += 1
                    continue
                res = await meta_client.consultar_publicacao(
                    plataforma=alvo["plataforma"],
                    token=token,
                    container_id=alvo["container_id"],
                    post_external_id=alvo["post_external_id"],
                    provedor=provedor,
                )
                await _postagens.registrar_resultado(
                    s,
                    alvo["id"],
                    status=STATUS_PUBLICADO if res.ok else STATUS_REVISAR,
                    result=(
                        res.erro or f"reconciliado: a Meta confirmou a publicação ({ident})"
                    )[:2000],
                    post_external_id=res.post_external_id or alvo["post_external_id"],
                    post_url=res.post_url or alvo["post_url"],
                    container_id=res.container_id or alvo["container_id"],
                )
                resolvidas += 1 if res.ok else 0
                revisar += 0 if res.ok else 1
            except Exception as e:  # noqa: BLE001
                await s.rollback()
                logger.error(
                    "marketing_postagens_reconciliar_item_failed",
                    postagem=str(alvo["id"]),
                    err=str(e)[:300],
                )
    logger.info(
        "marketing_postagens_reconciliar_tick",
        presas=len(alvos),
        publicadas=resolvidas,
        revisar=revisar,
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
        # Sem horário escolhido, vale o padrão. Antes a varredura exigia
        # `daily_sync_time` preenchido e ficava em silêncio: em 22/09/2026 os
        # dois usuários com a varredura LIGADA estavam sem horário e ela não
        # rodava para ninguém havia meses — o dono de 1.809 produtos nem sequer
        # tinha ligado. Ligar sem escolher hora agora faz o que a pessoa espera.
        hora = sa_func.coalesce(UserSettings.daily_sync_time, DAILY_SYNC_HORA_PADRAO)
        rows = await s.execute(
            select(UserSettings).where(
                UserSettings.daily_sync_enabled.is_(True),
                hora >= window_start,
                hora <= window_end,
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
            pool = await get_arq_sync_pool()
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
    atualizado.

    Em seguida propaga esse custo fresco pra `import_products.custo_bling`
    (casando por SKU) — a aba Importação passa a seguir o Bling sozinha,
    igual à Tabela de Preços, sem ajuste manual."""
    async with session_scope() as s:
        summary = await run_sync_product_bling_costs(s)
    logger.info("product_bling_cost_sync_done", **summary)
    # Com o custo dos produtos já fresco, re-carimba pedidos recentes que
    # entraram com preco_custo NULL (SKU novo cujo custo só chegou agora).
    # Sessão nova: o sync de products já commitou.
    async with session_scope() as s:
        restamp_summary = await run_restamp_order_costs(s)
    logger.info("order_cost_restamp_done", **restamp_summary)
    # Sessão nova: o sync de products já commitou. Se a propagação pra
    # Importação falhar, o refresh de products permanece.
    async with session_scope() as s:
        import_summary = await run_sync_import_bling_costs(s)
    logger.info("import_bling_cost_sync_done", **import_summary)


async def kit_components_sync(ctx: dict) -> None:
    """Semanal: regrava `bling_kit_components` lendo a estrutura de cada kit
    ativo (formato='E') no Bling. O order-lookup de devoluções usa esse cache
    pra explodir um SKU de kit nos componentes e devolver estoque ao produto
    certo. A estrutura muda raramente, por isso semanal."""
    async with session_scope() as s:
        summary = await run_sync_kit_components(s)
    logger.info("kit_components_sync_done", **summary)


async def valuation_estoque_snapshot(ctx: dict) -> None:
    """Diário (~08h BRT): crawl do Bling pra gravar o snapshot de estoque por
    local (PI/SA/SP/RA/CD/CI/US/Eletro/Mala/Outros) em
    `valuation_estoque_bling_diario`. Também atualiza `valuation.estoque`
    (total) — substitui a rotina externa estoque-bling-diario que mandava o
    breakdown no Threema. A aba "Estoque Bling" da página
    /financeiro/valuation lê esta tabela."""
    async with session_scope() as s:
        summary = await run_valuation_estoque_snapshot(s)
    logger.info("valuation_estoque_snapshot_done", **summary)


async def valuation_estoque_catchup(ctx: dict) -> None:
    """Toda hora (:20) + no startup do worker: repõe o snapshot de estoque do
    dia se o cron das 08:00 BRT foi perdido (worker reiniciado por deploy na
    janela — 02/09, 11/09 e 14/09 ficaram sem linha). Quando a linha já
    existe, ou ainda não deu 08:00, é só um SELECT."""
    async with session_scope() as s:
        summary = await repor_snapshot_se_faltar(s)
    logger.info("valuation_estoque_catchup_done", **summary)


async def logistica_ml_ingest(ctx: dict) -> None:
    """De hora em hora (:00 — era 1x/dia; pedido do usuário 25/08): importa os
    novos pedidos Mercado Livre pra aba Logística, realinha o status Bling,
    limpa os finalizados (Cancelado/Resolvido/Perdimento e Entregue +90d) e
    enriquece o status do Meli. Assim a lista cresce e se limpa sozinha."""
    async with session_scope() as s:
        summary = await run_ingest_ml_daily(s)
    logger.info("logistica_ml_ingest_done", **summary)


async def logistica_marketplaces_ingest(ctx: dict) -> None:
    """De hora em hora (:05, junto do ML): importa os novos pedidos Shopee/
    TikTok/Amazon pra aba Logística (só ingestão — esses marketplaces ainda não
    têm enriquecimento de Status Plataforma)."""
    async with session_scope() as s:
        summary = await run_ingest_marketplaces_daily(s)
    logger.info("logistica_marketplaces_ingest_done", **summary)


async def devolucao_rastreio_sync(ctx: dict) -> None:
    """A cada 30 min: rastreio/status do pacote que VOLTA nos pedidos em
    Aguardando Devolução (returns API de TikTok/Shopee/ML → devolucao_rastreio
    + 17track). Eduardo 03/09: "precisa sempre estar atualizadinho"."""
    from app.services import devolucao_rastreio_sync as svc  # tardio: módulo pesado

    async with session_scope() as s:
        summary = await svc.run(s)
    logger.info("devolucao_rastreio_sync_job_done", **summary)


async def logistica_track_sync(ctx: dict) -> None:
    """A cada 15 min: registra no 17track o rastreio Correios novo da Logística e
    puxa a localização física real. Eduardo 04/09: "rastreio e localização de
    correios não está atualizando... sempre que mudar, precisa atualizar em
    tempo real". O tempo real é o push do webhook; isto garante que o número
    esteja registrado (sem registro o 17track nunca empurra nada) e serve de
    rede de segurança se o push falhar."""
    from app.services import logistica_track_sync as svc  # tardio: httpx + models

    async with session_scope() as s:
        summary = await svc.run(s)
    logger.info("logistica_track_sync_job_done", **summary)


async def devolucao_prazo_sync(ctx: dict) -> None:
    """A cada 30 min: pergunta à Shopee/TikTok até quando dá pra contestar cada
    devolução recente e avisa no Threema quando o motivo ainda está vazio a
    menos de 24 h do prazo (Eduardo 09/09, caso 291835)."""
    from app.services import devolucao_prazo as svc  # tardio: httpx + models

    async with session_scope() as s:
        summary = await svc.run(s)
    logger.info("devolucao_prazo_sync_job_done", **summary)


async def logistica_track_forcar(ctx: dict) -> None:
    """07:00 e 16:30 (Brasília): força o 17track a reconsultar os Correios dos
    pacotes em trânsito. O 17track sozinho só consulta ~1x/dia; Eduardo (08/09,
    pedido 294036) viu os Correios com movimento das 06:54 e a Logística parada
    no evento de 4 dias antes. Grátis na 1ª vez por número (retomar), 1 crédito
    nas seguintes (apagar + registrar)."""
    from app.services import logistica_track_sync as svc  # tardio: httpx + models

    async with session_scope() as s:
        summary = await svc.forcar_reconsulta(s)
    logger.info("logistica_track_forcar_job_done", **summary)


async def logistica_amazon_avisos(ctx: dict) -> None:
    """08:06 e 16:06 (Brasília): avisos Threema do Envio próprio da Amazon —
    previsão dos Correios vencida, faltam 3 dias para a data máxima da Amazon,
    data máxima vencida (Vinicius 15/09: passado o prazo a Amazon reembolsa o
    cliente e não dá tempo de acionar os Correios). Um aviso por pedido e
    tipo; quem recebe é o cadastro do botão Informar da aba Amazon."""
    from app.services import logistica_amazon_avisos as svc  # tardio: models

    async with session_scope() as s:
        summary = await svc.run(s)
    logger.info("logistica_amazon_avisos_done", **summary)


async def logistica_cliente_mensagens(ctx: dict) -> None:
    """A cada 15 min (:08/:23/:38/:53): manda ao comprador da Amazon, por
    e-mail pro endereço de retransmissão, o que ainda não foi mandado —
    problema nos Correios, previsão vencida, entrega. No-op enquanto
    `amazon_mensagens_cliente` estiver desligado no .env."""
    from app.services import logistica_cliente_mensagens as svc  # tardio: models

    async with session_scope() as s:
        summary = await svc.run(s)
    logger.info("logistica_cliente_mensagens_done", **summary)


async def chamados_replica_automatica(ctx: dict) -> None:
    """De hora em hora (:25): réplica automática dos Chamados (reenvia a
    mensagem cadastrada a cada N dias enquanto ligada) + monitoramento (fecha
    sozinho o chamado de API quando o ML encerra o claim) + retentativa das
    aberturas automáticas de devolução no ML que ficaram pendentes (ML ainda
    não liberou a revisão, foto anexada depois, sub-motivo escolhido depois)."""
    async with session_scope() as s:
        summary = await run_chamados_replica_automatica(s)
    logger.info("chamados_replica_automatica_done", **summary)
    async with session_scope() as s:
        pend = await chamados_devolucao.processar_pendentes(s)
    logger.info("chamados_devolucao_pendentes_done", **pend)
    # Mensagem ao comprador (senha do produto travado): o que não saiu na hora,
    # e a varredura dos lançamentos que nunca tiveram pedido de senha (os
    # anteriores a 22/09 e os que o gancho do save não pegou).
    async with session_scope() as s:
        msgs = await devolucao_mensagem_comprador.processar_pendentes(s)
    logger.info("devolucao_mensagem_comprador_pendentes_done", **msgs)
    async with session_scope() as s:
        varridas = await devolucao_mensagem_comprador.varrer_sem_mensagem(s)
    logger.info("devolucao_mensagem_comprador_varredura_done", **varridas)
    # Resposta da plataforma (TikTok/Shopee/ML) cai no histórico e fecha o chamado.
    async with session_scope() as s:
        resp = await chamados_devolucao_sync.sync_respostas(s)
    logger.info("chamados_devolucao_sync_done", **resp)
    # 17/09: abertura que a API não consegue fazer (sem return, sem foto…) parava
    # aqui pra sempre. A varredura roteia pro robô ou pro humano e avisa no Threema.
    async with session_scope() as s:
        presas = await chamados_pendencias.varrer(s)
    logger.info(
        "chamados_pendencias_done",
        **{k: v for k, v in presas.items() if k != "avisos"},
    )


async def chamados_tiktok_reembolso_vigia(ctx: dict) -> None:
    """A cada 30 min (:10/:40): vigia do SÓ REEMBOLSO na TikTok — avisa no Threema
    faltando 12 h e 3 h sem resposta e registra o desfecho no chamado (Vinicius 18/09:
    o caso cai em Devoluções › Fraude e é o LANÇAMENTO que responde a TikTok; o vigia
    não abre chamado nem contesta sozinho). Origem: 294865 aprovado pela TikTok por
    falta de resposta, R$ 744 (Eduardo 16/09)."""
    from app.services import chamados_tiktok_reembolso

    try:
        async with session_scope() as s:
            summary = await chamados_tiktok_reembolso.run_vigia(s)
    except Exception as e:  # noqa: BLE001
        logger.warning("chamados_tiktok_reembolso_falhou", err=str(e)[:300])
        return
    logger.info("chamados_tiktok_reembolso_done", **summary)


async def bling_situacoes_sync(ctx: dict) -> None:
    """1x/dia (08:50 UTC = 05:50 BRT) e no startup do worker: catálogo
    `situacao_bling` igual ao módulo Vendas do Bling — situação nova entra,
    apagada vira inativa e some dos dropdowns dos Chamados/Logística (Eduardo
    15/09: "tem situação ali que nem existe mais, ex. Enviado Geral CI").
    Best-effort: sem integração Bling ou API fora, só loga."""
    try:
        async with session_scope() as s:
            summary = await sync_situacoes_bling(s)
    except Exception as e:  # noqa: BLE001
        logger.warning("bling_situacoes_sync_failed", err=str(e)[:300])
        return
    logger.info("bling_situacoes_sync_done", **summary)


async def chamado_devolucao_disparar(ctx: dict, chamado_id: str) -> None:
    """Abre no Mercado Livre o chamado automático de uma devolução (revisão da
    devolução com problema, com as fotos da linha). Enfileirado pelo router
    de devoluções ao marcar o motivo / anexar foto; o cron :25 retenta o que
    ficar pendente."""
    async with session_scope() as s:
        # 17/09: create + fotos agora podem gerar 2 jobs (o 2º adiado) — um de cada
        # vez; o 2º vê a abertura já `enviada` e não contesta de novo.
        await s.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
            {"k": f"chamado_devolucao_disparar:{chamado_id}"},
        )
        msg = await chamados_devolucao.disparar_por_id(s, UUID(chamado_id))
        await s.commit()
    logger.info(
        "chamado_devolucao_disparar_done",
        chamado_id=chamado_id,
        status=(msg.status if msg is not None else None),
        erro=(msg.erro if msg is not None else None),
    )


async def devolucao_mensagem_comprador_enviar(ctx: dict, linha_id: str) -> None:
    """Manda ao comprador, no chat da Shopee, o pedido da senha do produto que
    voltou travado (motivo "Bloqueado"). Enfileirado pelo router de devoluções
    ao marcar o motivo / anexar foto; o cron :25 retenta o que ficar pendente."""
    async with session_scope() as s:
        # Mesmo motivo do disparo da contestação: create + upload de foto podem
        # gerar dois jobs do mesmo pedido — um de cada vez, e o segundo vê a
        # linha já `enviada` e não manda nada.
        await s.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
            {"k": f"devolucao_mensagem_comprador:{linha_id}"},
        )
        linha = await devolucao_mensagem_comprador.enviar_por_id(s, linha_id)
        await s.commit()
    logger.info(
        "devolucao_mensagem_comprador_done",
        linha_id=linha_id,
        status=(linha.status if linha is not None else None),
        erro=(linha.erro if linha is not None else None),
    )


async def pos_vendas_notas_sync(ctx: dict) -> None:
    """A cada 10 min: espelha as NF-e das contas `bling_notas` em
    `bling_notas_emitidas` (lista da janela + valor via detalhe com teto por
    rodada + CNPJ/emitente da conta 1x). A página Pós Vendas casa pedido ↔
    notas só no banco — sem Bling ao vivo na listagem."""
    async with session_scope() as s:
        summary = await run_pos_vendas_sync(s)
    logger.info("pos_vendas_notas_sync_done", **summary)


# Trava da recarga da Logística (redis): impede duas recargas AO MESMO TEMPO
# (clique do botão + cron dos 5 min, ou dois cliques). Quem chega segundo pula
# e devolve `pulado_ja_rodando` no resumo — o front mostra um aviso amigável em
# vez de rodar de novo à toa. O TTL solta a trava sozinho se o worker morrer no
# meio (deploy/kill) sem executar o finally; a recarga normal leva ~1,5-2 min.
_LOGISTICA_RECARREGAR_LOCK_KEY = "davinci:lock:logistica_recarregar"
# TTL curto + RENOVAÇÃO enquanto o job vive (`_trava_com_heartbeat`): o worker
# é recriado ~25×/dia por deploy e antes a trava órfã (TTL 600s/3600s) barrava
# as rodadas seguintes por até 10 min — motor parado justamente depois de
# publicar. Agora um worker morto solta a trava em ≤3 min (Eduardo, 15/09:
# "precisamos que sempre rode isso automaticamente").
_LOCK_TTL_S = 180
_LOCK_HEARTBEAT_S = 60
# Carimbo do último sucesso de cada job da Logística (vigia lê daqui).
_LOGISTICA_OK_KEY = "davinci:logistica:ultimo_ok"
# Primeira vez que o vigia viu cada job (carência). Fica SEPARADO do carimbo
# de sucesso: gravar "agora" no carimbo de sucesso faria um job que NUNCA
# rodou parecer saudável — foi o que aconteceu às 14:13 de 15/09, quando as 4
# varreduras apareceram "com sucesso há 1 min" sem nenhuma ter terminado.
_LOGISTICA_VISTO_KEY = "davinci:logistica:primeira_observacao"


@asynccontextmanager
async def _trava_com_heartbeat(redis, key: str, *, nome: str):
    """Trava no redis que se renova sozinha enquanto o job roda.

    Cede `True` quando pegou a trava e `False` quando outro job já está com
    ela. Sem redis (testes), roda sem trava. A renovação evita os dois
    extremos: TTL longo deixa trava órfã depois de um kill; TTL curto deixaria
    duas rodadas em paralelo num job lento."""
    if redis is None:
        yield True
        return
    got = await redis.set(key, "1", nx=True, ex=_LOCK_TTL_S)
    if not got:
        yield False
        return

    async def _renovar() -> None:
        while True:
            await asyncio.sleep(_LOCK_HEARTBEAT_S)
            try:
                await redis.expire(key, _LOCK_TTL_S)
            except Exception:  # noqa: BLE001 — perder a renovação só encurta a trava
                logger.warning("logistica_lock_heartbeat_falhou", job=nome)
                return

    tarefa = asyncio.create_task(_renovar())
    try:
        yield True
    finally:
        tarefa.cancel()
        try:
            await redis.delete(key)
        except Exception:  # noqa: BLE001 — o TTL solta a trava sozinho
            logger.warning("logistica_lock_release_falhou", job=nome)


async def _marcar_ok(redis, job: str) -> None:
    """Carimba o último sucesso do job (o vigia avisa quando envelhece)."""
    if redis is None:
        return
    try:
        await redis.hset(_LOGISTICA_OK_KEY, job, str(int(datetime.now(UTC).timestamp())))
    except Exception:  # noqa: BLE001 — carimbo é acessório
        logger.warning("logistica_carimbo_ok_falhou", job=job)


async def logistica_recarregar(ctx: dict) -> dict[str, int]:
    """Motor da Logística: re-enriquece o Status Plataforma das linhas PENDENTES
    do painel (ML/Shopee/TikTok/Amazon) e aplica no Bling a mudança de situação
    das que casam uma regra da aba Status. Roda de DOIS jeitos — sob demanda
    (botão "recarregar" das abas de marketplace) e sozinho a cada 5 min (cron;
    pedido do usuário 26/08: a coluna Status Plataforma tem que refletir a
    mudança sem ninguém precisar clicar). A trava no redis garante uma recarga
    por vez; o aplicar em lote já é idempotente (só age quando a transição parte
    do estado atual), então mesmo um overlap raro não bagunça. RETORNA o resumo
    — o arq guarda o result e o GET /recarregar/{job_id} o entrega pro toast do
    front (sem o return o resumo chegava vazio)."""
    redis = ctx.get("redis")
    async with _trava_com_heartbeat(
        redis, _LOGISTICA_RECARREGAR_LOCK_KEY, nome="recarregar"
    ) as livre:
        if not livre:
            logger.info("logistica_recarregar_pulado", reason="ja_em_andamento")
            return {"pulado_ja_rodando": 1}
        async with session_scope() as s:
            summary = await recarregar_ml(s)
        await _marcar_ok(redis, "recarregar")
        logger.info("logistica_recarregar_done", **summary)
        return summary


_LOGISTICA_SWEEPS_LOCK_KEY = "davinci:lock:logistica_sweeps"


async def _sweep_de(ctx: dict, plataforma: str) -> dict[str, int]:
    """Varredura de pós-venda de UMA plataforma (janela de 45 dias): quem
    mudou de vida depois de escondido volta pro motor.

    Uma plataforma por vez, em horários diferentes: junto (as 4 numa rodada
    só) a passada levava minutos e morria em todo deploy — em 15/09 a rodada
    das :39 foi morta pela publicação das 13:41 sem terminar nenhuma. Separado,
    cada varredura termina rápido e a morte de uma não leva as outras."""
    redis = ctx.get("redis")
    async with _trava_com_heartbeat(
        redis, f"{_LOGISTICA_SWEEPS_LOCK_KEY}:{plataforma}", nome=f"sweep:{plataforma}"
    ) as livre:
        if not livre:
            logger.info("logistica_sweep_pulado", plataforma=plataforma)
            return {"pulado_ja_rodando": 1}
        async with session_scope() as s:
            summary = await sweeps_pos_venda(s, apenas=plataforma)
        await _marcar_ok(redis, f"sweep:{plataforma}")
        logger.info("logistica_sweep_done", plataforma=plataforma, **summary)
        return summary


async def logistica_sweep_shopee(ctx: dict) -> dict[str, int]:
    return await _sweep_de(ctx, "shopee")


async def logistica_sweep_tiktok(ctx: dict) -> dict[str, int]:
    return await _sweep_de(ctx, "tiktok")


async def logistica_sweep_ml(ctx: dict) -> dict[str, int]:
    return await _sweep_de(ctx, "ml")


async def logistica_sweep_amazon(ctx: dict) -> dict[str, int]:
    return await _sweep_de(ctx, "amazon")


# Quanto tempo sem sucesso antes de avisar, por job. Motor: 5 min de cron +
# folga pra uma rodada longa e um deploy no meio. Varreduras: 1×/h cada.
_LOGISTICA_VIGIA_LIMITES_MIN = {
    "recarregar": 20,
    "sweep:shopee": 150,
    "sweep:tiktok": 150,
    "sweep:ml": 150,
    "sweep:amazon": 150,
}


async def logistica_vigia(ctx: dict) -> dict[str, int]:
    """Vigia da automação da Logística (Eduardo, 15/09: "precisamos que sempre
    rode isso automaticamente"): compara o carimbo do último sucesso de cada
    job com o limite e avisa no Threema + sino quando passa. Sem isso, um
    motor parado só aparecia quando alguém estranhava a tela velha.

    Carimbo ausente (redis reiniciado, primeira subida) NÃO alerta: marca a
    hora atual e espera o próximo ciclo."""
    redis = ctx.get("redis")
    if redis is None:
        return {"sem_redis": 1}
    agora = datetime.now(UTC)
    atrasados: list[str] = []
    for job, limite_min in _LOGISTICA_VIGIA_LIMITES_MIN.items():
        try:
            bruto = await redis.hget(_LOGISTICA_OK_KEY, job)
        except Exception:  # noqa: BLE001
            logger.warning("logistica_vigia_redis_falhou", job=job)
            return {"erro_redis": 1}
        if bruto is None:
            # Nunca teve sucesso: conta o atraso desde a PRIMEIRA vez que o
            # vigia viu o job (carência), não desde agora.
            visto = await redis.hget(_LOGISTICA_VISTO_KEY, job)
            if visto is None:
                await redis.hset(
                    _LOGISTICA_VISTO_KEY, job, str(int(agora.timestamp()))
                )
                continue
            ts = datetime.fromtimestamp(int(visto), tz=UTC)
            atraso_min = int((agora - ts).total_seconds() // 60)
            if atraso_min > limite_min:
                atrasados.append(
                    f"{job}: NUNCA concluiu (visto há {atraso_min} min, limite {limite_min})"
                )
            continue
        ts = datetime.fromtimestamp(int(bruto), tz=UTC)
        atraso_min = int((agora - ts).total_seconds() // 60)
        if atraso_min > limite_min:
            atrasados.append(f"{job}: último sucesso há {atraso_min} min (limite {limite_min})")
    if not atrasados:
        return {"ok": len(_LOGISTICA_VIGIA_LIMITES_MIN)}
    texto = "Logística — automação parada:\n" + "\n".join(atrasados)
    logger.warning("logistica_vigia_atrasado", atrasados=atrasados)
    destinos = threema.parse_recipients(get_settings().nf_sem_estoque_threema_recipients)
    if destinos:
        try:
            await threema.ThreemaClient(contexto="logistica").send_to_all(texto, destinos)
        except Exception as exc:  # noqa: BLE001 — aviso é best-effort
            logger.warning("logistica_vigia_threema_falhou", erro=str(exc)[:200])
    async with session_scope() as s:
        # Sino pros admins, com dedupe por HORA: um aviso por hora, não a cada
        # tick (o vigia roda 2×/hora).
        admin_ids = (
            await s.execute(select(User.id).where(User.role == UserRole.ADMIN))
        ).scalars().all()
        for uid in admin_ids:
            await emit_alert(
                s,
                user_id=uid,
                type=AlertType.SYNC_FAILURE,
                severity=AlertSeverity.WARNING,
                title="Logística: automação parada",
                message=texto,
                dedupe_key=f"logistica_vigia:{uid}:{agora:%Y%m%d%H}",
                notify_telegram=False,  # o aviso já foi pelo Threema
            )
    return {"atrasados": len(atrasados), "admins": len(admin_ids)}


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
                    # TikTok stores `token_expires_at`; Shopee/ML use
                    # `expires_at`. Read whichever is present so the column
                    # mirrors the real expiry across platforms.
                    exp = new_creds.get("token_expires_at") or new_creds.get("expires_at")
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


async def bling_notas_token_refresh(ctx: dict) -> None:
    """Refresh dos tokens das contas `bling_notas` (apps OAuth de emissão
    de NF, separados da integração principal). AT do Bling dura 6h; o cron
    a cada 5h (00/05/10/15/20 UTC) mantém o token sempre válido com uma
    única chamada /oauth/token por conta por ciclo."""
    async with session_scope() as s:
        summary = await run_refresh_bling_notas_tokens(s)
    logger.info("bling_notas_token_refresh_done", **summary)


async def shopee_token_refresh(ctx: dict) -> None:
    """Refresh Shopee tokens expiring within 6h (Shopee tokens last 4h)."""
    await _refresh_tokens_for(IntegrationPlatform.SHOPEE, expiring_within_s=6 * 3600)


async def ml_token_refresh(ctx: dict) -> None:
    """Refresh Mercado Livre tokens expiring within 2h (ML access tokens last 6h)."""
    await _refresh_tokens_for(IntegrationPlatform.ML, expiring_within_s=2 * 3600)


async def tiktok_token_refresh(ctx: dict) -> None:
    """Refresh TikTok tokens expiring within 12h (TikTok AT lasts ~24h)."""
    await _refresh_tokens_for(IntegrationPlatform.TIKTOK, expiring_within_s=12 * 3600)


async def meta_token_refresh(ctx: dict) -> None:
    """Tokens das contas de rede social (robô de postagem) vencendo em 7 dias.

    Diferente dos irmãos acima, este NÃO renova — ainda: o token de Página da
    Meta é de longa duração (~60 dias) e a troca (`grant_type=fb_exchange_
    token`) exige app_id/app_secret do app da Meta, que não estão no settings.
    Quando entrarem, a renovação cabe exatamente aqui. Por ora ele faz o que dá
    pra fazer sem segredo nenhum: marca `expirado` o que já venceu e AVISA os
    admins com uma semana de antecedência — senão a primeira notícia de token
    vencido seria um post que não saiu.

    `redes_sociais_tokens.token_expires_at` está em claro justamente pra esta
    varredura não precisar decifrar nada (mesma escolha das integrações). O
    token em si não é lido aqui."""
    if not _settings.enable_marketing:
        return
    from app.models import RedeSocial, RedeSocialToken

    agora = datetime.now(UTC)
    limite = agora + timedelta(days=7)
    async with session_scope() as s:
        linhas = (
            await s.execute(
                select(RedeSocialToken, RedeSocial)
                .join(RedeSocial, RedeSocial.id == RedeSocialToken.rede_social_id)
                .where(
                    RedeSocialToken.token_expires_at.is_not(None),
                    RedeSocialToken.token_expires_at <= limite,
                    # Revogado já foi avisado e não volta sozinho.
                    RedeSocialToken.status != "revogado",
                )
                .order_by(RedeSocialToken.token_expires_at.asc())
            )
        ).all()
        if not linhas:
            return
        admin_ids = (
            await s.execute(select(User.id).where(User.role == UserRole.ADMIN))
        ).scalars().all()
        avisados = 0
        for tok, rede in linhas:
            vencido = tok.token_expires_at <= agora
            if vencido and tok.status != "expirado":
                tok.status = "expirado"
                tok.last_error = "token vencido — reconecte a conta"
            conta = rede.conta or rede.usuario or rede.plataforma
            dias = max(0, (tok.token_expires_at - agora).days)
            texto = (
                f"O token de {conta} ({rede.plataforma}) "
                + ("VENCEU" if vencido else f"vence em {dias} dia(s)")
                + ". Enquanto isso o robô não consegue publicar nessa conta: "
                "reconecte em Cadastros › Redes Sociais."
            )
            for uid in admin_ids:
                # Dedupe por DIA: um aviso por conta por dia até alguém
                # reconectar (o cron roda 1×/dia, mas um restart não duplica).
                a = await emit_alert(
                    s,
                    user_id=uid,
                    type=AlertType.GENERIC,
                    severity=AlertSeverity.ERROR if vencido else AlertSeverity.WARNING,
                    title=f"Rede social: token {'vencido' if vencido else 'vencendo'} ({conta})",
                    message=texto,
                    payload={
                        "rede_social_id": str(rede.id),
                        "plataforma": rede.plataforma,
                        "expira_em": tok.token_expires_at.isoformat(),
                    },
                    dedupe_key=f"rede_social_token:{tok.id}:{agora:%Y%m%d}",
                    notify_telegram=False,
                )
                if a is not None:
                    avisados += 1
        await s.commit()
    logger.info("meta_token_refresh_done", contas=len(linhas), avisados=avisados)


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


async def tuta_devolucoes_tick(ctx: dict) -> None:
    """07:00 BRT — manda o robô do Mac ler a caixa do Tuta e avisa o thatcher
    no Threema com os códigos de devolução do dia.

    Passa pelo robô porque o Tuta não tem IMAP nem API pública e as regras de
    caixa dele não encaminham para fora — ver services/tuta_devolucoes.py."""
    from app.services import tuta_devolucoes

    async with session_scope() as s:
        cmd = await tuta_devolucoes.enfileirar(s)
    logger.info("tuta_devolucoes_tick_done", enfileirado=bool(cmd))


async def pricing_confirmacao_amazon_tick(ctx: dict) -> None:
    """A cada 10 min: lê na Amazon o preço VIVO dos anúncios enviados pela
    Tabela de Preços e avisa se a loja não aplicou o que o DaVinci mandou.
    Ver services/pricing_confirmacao_amazon.py."""
    from app.services.pricing_confirmacao_amazon import confirmar_pushes_recentes

    async with session_scope() as s:
        result = await confirmar_pushes_recentes(s)
    logger.info("pricing_confirmacao_amazon_tick_done", **result)


async def marketplace_financials_esteira_lenta(ctx: dict) -> None:
    """Fila dos pedidos cujo financeiro esbarrou na API (403/429/5xx/sem token)
    ou num repasse que a plataforma ainda não publicou.

    Separada da fila rápida de propósito: na queda de setembro/2026 ~3.900
    pedidos entraram nesse estado, e num único backoff esse backlog comeria as
    vagas do ciclo e os pedidos do DIA ficariam sem Frete/Taxa na Margem."""
    from app.services.marketplace_financials import run_esteira_lenta_financials

    async with session_scope() as s:
        result = await run_esteira_lenta_financials(s, limit=80)
    logger.info("marketplace_financials_esteira_lenta_done", **result)


async def marketplace_financials_ressuscitar(ctx: dict) -> None:
    """Reabre a fila dos pedidos que morreram por falha de API.

    `next_retry_at = NULL` num status retentável = linha fora da fila pra
    sempre. Depois de qualquer queda de token isso deixava a Margem em branco
    mesmo com a API de volta. Este tick devolve essas linhas à esteira lenta,
    escalonadas — o sistema se recupera sozinho, sem UPDATE manual no banco."""
    from app.services.marketplace_financials import run_ressuscitar_financials

    async with session_scope() as s:
        result = await run_ressuscitar_financials(s, limit=400)
    logger.info("marketplace_financials_ressuscitar_done", **result)


async def tiktok_unsettled_sweep(ctx: dict) -> None:
    """Estimativa oficial pré-liquidação do TikTok (a mesma da Central do
    Vendedor) para os financeiros ainda sem settlement real — 1-2 chamadas
    por loja preenchem o Saldo Plataforma da Margem no dia da venda em vez
    de dias depois. Ver run_tiktok_unsettled_sweep.

    Achou valor novo → rebuild do snapshot NA SEQUÊNCIA (Eduardo, 01/09:
    "precisamos pegar isso estantaneo" — caso 293707: o valor já estava no
    mof desde o tick :38 e a aba seguia em branco esperando o rebuild de
    :45/refresh da página). Sem update, sem rebuild: o tick de 10min não
    paga 20d de rebuild à toa."""
    from app.services.marketplace_financials import run_tiktok_unsettled_sweep
    from app.services.verificar_margem import rebuild_all

    async with session_scope() as s:
        try:
            result = await run_tiktok_unsettled_sweep(s)
        except Exception as e:  # noqa: BLE001
            logger.warning("tiktok_unsettled_sweep_failed", error=str(e)[:300])
            return
    logger.info("tiktok_unsettled_sweep_done", **result)

    if not result.get("updated"):
        return
    # Sessão própria: mesmo padrão do verificar_margem_snapshot (o rebuild
    # tem advisory lock e commit interno; não convive com a sessão do sweep).
    async with session_scope() as s:
        try:
            n = await rebuild_all(s)
            logger.info("tiktok_unsettled_sweep_snapshot", rebuilt=n)
        except Exception as e:  # noqa: BLE001
            logger.warning("tiktok_unsettled_sweep_snapshot_failed", error=str(e)[:200])


async def tiktok_unsettled_fast_lane(ctx: dict) -> None:
    """Via expressa MINUTO A MINUTO da estimativa TikTok (Eduardo, 01/09:
    "tem que ficar o mais rapido possivel... nao pode mais ficar 10 min" —
    a aprovação da Margem vai virar automática e cada minuto conta).

    Só age em pedidos criados nas últimas 3h que AINDA não têm estimativa:
    na maioria dos minutos é 1 SELECT e zero chamadas ao TikTok; quando há
    pedido novo aguardando, 1 página por loja afetada (o TikTok libera a
    estimativa ~30-60min após a venda — 293707 medido ao vivo — e o sort
    DESC a pega na primeira página assim que existir). Passou das 3h sem
    estimativa (caso raro, ex.: cancelado antes de pagar), sai daqui e fica
    com a varredura completa de 10min. Achou → snapshot na sequência: valor
    na aba no MESMO minuto."""
    from app.services.marketplace_financials import run_tiktok_unsettled_sweep
    from app.services.verificar_margem import rebuild_all

    async with session_scope() as s:
        try:
            result = await run_tiktok_unsettled_sweep(
                s, recent_hours=3, window_days=3, max_pages=1
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("tiktok_unsettled_fast_lane_failed", error=str(e)[:300])
            return
    if not result.get("updated"):
        return
    logger.info("tiktok_unsettled_fast_lane_done", **result)
    async with session_scope() as s:
        try:
            n = await rebuild_all(s)
            logger.info("tiktok_unsettled_fast_lane_snapshot", rebuilt=n)
        except Exception as e:  # noqa: BLE001
            logger.warning("tiktok_unsettled_fast_lane_snapshot_failed", error=str(e)[:200])


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
    """Mark `running` jobs with no heartbeat in 5min as failed, e poda os
    registros duráveis de ingest de pedido já resolvidos.

    Retenção do type=ingest_bling_order (introduzido p/ não perder webhook):
    todo webhook de pedido grava uma linha, então sem poda a tabela cresceria
    como o sync_product já cresce (~400k+ linhas, sem retenção própria). Só
    este tipo é podado aqui — SUCCEEDED/CANCELLED após 3 dias (já cumpriram o
    papel) e FAILED após 7 dias (mantém visibilidade + além disso o backfill
    diário é a rede). PENDING/RUNNING (finished_at NULL) nunca são podados."""
    now = datetime.now(UTC)
    cutoff = now - timedelta(minutes=5)
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
                finished_at=now,
            )
        )
        pruned = await s.execute(
            delete(BackgroundJob).where(
                BackgroundJob.type == BackgroundJobType.INGEST_BLING_ORDER,
                BackgroundJob.finished_at.is_not(None),
                or_(
                    and_(
                        BackgroundJob.status.in_(
                            [
                                BackgroundJobStatus.SUCCEEDED,
                                BackgroundJobStatus.CANCELLED,
                            ]
                        ),
                        BackgroundJob.finished_at < now - timedelta(days=3),
                    ),
                    and_(
                        BackgroundJob.status == BackgroundJobStatus.FAILED,
                        BackgroundJob.finished_at < now - timedelta(days=7),
                    ),
                ),
            )
        )
        logger.info(
            "background_jobs_gc_done",
            marked_failed=result.rowcount or 0,
            ingest_pruned=pruned.rowcount or 0,
        )


def _next_month_partition_bounds(now: datetime) -> tuple[str, str, str]:
    """Returns (partition_name, start_iso_date, end_iso_date) for month N+1
    relative to `now` (UTC)."""
    first = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0, tzinfo=UTC)
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
    """Delete alerts older than 60 days (B10). Carona diária da Ouvidoria:
    rodadas com mais de 30 dias (`ouvidoria_rodadas` cresce centenas de linhas
    por dia com os 7 robôs) e ocorrências FECHADAS há mais de 180 dias — menos
    as `ignorada`, que são a memória de "não cobre mais isto" e, apagadas,
    fariam o robô reabrir a linha."""
    cutoff = datetime.now(UTC) - timedelta(days=60)
    async with session_scope() as s:
        result = await s.execute(delete(Alert).where(Alert.created_at < cutoff))
        logger.info("alerts_cleanup_done", deleted=result.rowcount or 0)
    try:
        async with session_scope() as s:
            n = await ouvidoria_gc_rodadas(s, dias=30)
        logger.info("ouvidoria_gc_rodadas_done", deleted=n)
    except Exception:  # noqa: BLE001 — limpeza não pode derrubar o tick
        logger.exception("ouvidoria_gc_rodadas_unhandled")
    try:
        async with session_scope() as s:
            n = await ouvidoria_gc_ocorrencias(s)
        logger.info("ouvidoria_gc_ocorrencias_done", deleted=n)
    except Exception:  # noqa: BLE001 — limpeza não pode derrubar o tick
        logger.exception("ouvidoria_gc_ocorrencias_unhandled")


async def condicao_especial_gc(ctx: dict) -> None:
    """Apaga Condições Especiais de segmento cujo período terminou há mais de
    30 dias (pedido de 10/09: "quando acabar essa data pode excluir"). O painel
    já esconde no dia seguinte; a carência protege pedido feito dentro do
    período que ainda está em triagem. Ver services/condicao_especial."""
    from app.services.condicao_especial import limpar_encerradas

    hoje_sp = datetime.now(SP_TZ).date()
    async with session_scope() as s:
        n = await limpar_encerradas(s, hoje_sp=hoje_sp)
    logger.info("condicao_especial_gc_done", deleted=n)


async def verificar_margem_snapshot(ctx: dict) -> None:
    """Rebuild COMPLETO do snapshot davinci.verificar_margem (janela 20d) como
    backstop periódico (cron 30min).

    Antes era INSERT incremental (ON CONFLICT DO NOTHING) e nem rodava como
    cron. Virou rebuild_all porque o refresh per-ingest agora PULA os re-syncs
    (order.updated/safety_net/period_sync — ver bling_orders.
    _DEFER_MARGEM_REFRESH_EVENTS): as mudanças de status deles passam a chegar
    ao snapshot só via rebuild_all. O load da página /margem já reconstrói
    (throttle 5min), e este cron cobre os períodos sem ninguém olhando.
    Serializado pelo advisory lock do rebuild_all (sem herd)."""
    from app.services.margem_auto_hold import run as margem_auto_hold_run
    from app.services.verificar_margem import rebuild_all

    async with session_scope() as s:
        try:
            n = await rebuild_all(s)
            logger.info("verificar_margem_snapshot_done", rebuilt=n)
        except Exception as e:  # noqa: BLE001
            logger.warning("verificar_margem_snapshot_failed", error=str(e)[:200])
            return

    # Snapshot fresco → segura os pendentes "Em aberto" (Aguardando
    # Cancelamento + Observações no Bling). Sessão própria: o hold commita por
    # pedido e não deve conviver com o advisory lock/estado do rebuild.
    async with session_scope() as s:
        try:
            res = await margem_auto_hold_run(s)
            logger.info("margem_auto_hold_done", **res)
        except Exception as e:  # noqa: BLE001
            logger.warning("margem_auto_hold_cron_failed", error=str(e)[:200])


async def margem_reavaliar_reprovados(ctx: dict) -> None:
    """Revisita, de hora em hora, os pedidos que o robô da Margem reprovou e
    que ainda estão em Aguardando Cancelamento (Vinicius, 16/09/2026 — caso
    297400: reprovado com repasse provisório da Shopee, margem final passava
    pela Condição Especial). Rebusca o financeiro do pedido, refresca o
    snapshot e, se a margem oficial agora atende, devolve o pedido ao fluxo
    como o Aprovar faria. Detalhes em services/margem_auto_hold."""
    from app.services.margem_auto_hold import reavaliar_reprovados

    async with session_scope() as s:
        try:
            res = await reavaliar_reprovados(s)
            logger.info("margem_reavaliar_reprovados_done", **res)
        except Exception as e:  # noqa: BLE001
            logger.warning("margem_reavaliar_reprovados_failed", error=str(e)[:200])


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
    """Sweeps bling_orders rows stuck in etiqueta-enviada (situacao 21 'Em
    digitação' — canonical; 83965 'Enviado Etiqueta' — legacy) against
    Shopee/ML/Amazon shipment status. When a marketplace reports
    SHIPPED/shipped/Shipped, we bump Bling to situacao=15 ('Em andamento')
    via PATCH, then stamp em_andamento_data locally so the order surfaces
    in /controle-estoque's Pedidos and Envios tabs.

    Bling itself never auto-promotes 21/83965 → 15; carrier scans only update
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


async def nf_auto_enfileirar_tick(ctx: dict) -> None:
    """Sweep do auto-enfileirador de NF (pedidos Shopee/TikTok "Em aberto").

    Confere o estoque antes de tudo (saldo negativo → Aguardando
    Cancelamento) e cria os NfCommand de import_avulsa — a mesma cadeia do
    botão "Enfileirar" do painel Faturamento, sem humano no gatilho.
    Gated pela flag nf_auto_enfileirar (default DESLIGADO); serializado por
    advisory xact lock dentro do service.
    """
    if not get_settings().nf_auto_enfileirar:
        logger.debug("nf_auto_enfileirar_disabled")
        return
    try:
        summary = await run_auto_enfileirar_nf()
        if (
            summary.get("enfileirados")
            or summary.get("sem_estoque")
            or summary.get("pulados")
        ):
            logger.info("nf_auto_enfileirar_done", **summary)
        else:
            logger.debug("nf_auto_enfileirar_noop", **summary)
    except Exception:  # noqa: BLE001
        logger.exception("nf_auto_enfileirar_unhandled")


async def prioridade_estoque_tick(ctx: dict) -> None:
    """Troca SKUs de pedidos "Em aberto" pra tag prioritária (coluna
    Prioridade da Tabela de Preços → Produtos). Eduardo (2026-08-27): "a tag
    que eu colocar la, o sku com a tag, ja deve trocar". SEM flag: coluna
    vazia = no-op barato (1 SELECT). Serializado por advisory xact lock
    dentro do service; guarda = só troca se o SKU alvo existe no Bling com
    saldo virtual suficiente.
    """
    try:
        summary = await prioridade_estoque_sweep()
        if summary.get("trocados") or summary.get("falhas"):
            logger.info("prioridade_estoque_done", **summary)
        else:
            logger.debug("prioridade_estoque_noop", **summary)
    except Exception:  # noqa: BLE001
        logger.exception("prioridade_estoque_unhandled")


async def prioridade_estoque_estorno_tick(ctx: dict) -> None:
    """2×/hora: manutenção das compensações de estoque dos kits trocados
    pela prioridade — retenta lançamentos que o Bling recusou, estorna os de
    pedidos cancelados/excluídos que nunca saíram e avisa (Threema) os que
    ficaram incertos. No-op barato quando não há nada."""
    try:
        summary = await manutencao_movimentos_sweep()
        if any(summary.values()):
            logger.info("prioridade_estoque_manutencao_done", **summary)
    except Exception:  # noqa: BLE001
        logger.exception("prioridade_estoque_manutencao_unhandled")


async def vigia_estoque_familia_tick(ctx: dict) -> None:
    """Confere se os anúncios estão mesmo publicando o total da família.

    Roda 2x por dia. Mede sempre; só manda mensagem quando existe anúncio
    parado no número antigo há mais de algumas horas E há destinatário
    configurado.
    """
    try:
        summary = await vigia_estoque_familia_sweep()
    except Exception:
        logger.exception("vigia_estoque_familia_unhandled")
        return
    if summary.get("atrasados"):
        logger.warning("vigia_estoque_familia_atrasados", **summary)
    else:
        logger.info("vigia_estoque_familia_ok", **summary)


async def vigia_importacao_tick(ctx: dict) -> None:
    """Vigia de importação (robô da Ouvidoria): pedido PAGO no ML / Shopee /
    TikTok / Amazon que não caiu no Bling → ocorrência + aviso Threema pra
    importar manualmente no canal multi loja (Eduardo, 2026-08-27 — a API
    pública do Bling não expõe essa tela). O modo vem da tela Ouvidoria ›
    Robôs: `desligado` sai sem rodar nem gravar rodada; `silencioso` roda e
    registra sem avisar (decidido dentro do sweep). O botão "Rodar agora"
    não passa por aqui, por isso o gate fica no tick e não no sweep.
    """
    try:
        async with session_scope() as s:
            modo = await ouvidoria_modo(s, "vigia_importacao")
        if modo == "desligado":
            logger.debug("vigia_importacao_desligado")
            return
        summary = await vigia_importacao_sweep()
        if summary.get("novas") or summary.get("avisadas") or summary.get(
            "contas_falha"
        ) or summary.get("sumiram"):
            logger.info("vigia_importacao_done", **summary)
        else:
            logger.debug("vigia_importacao_noop", **summary)
    except Exception:  # noqa: BLE001
        logger.exception("vigia_importacao_unhandled")


# ─── Ouvidoria: os 6 robôs de 22/09/2026 ───────────────────────────────────
# Todos no molde do `vigia_importacao_tick` acima: gate pelo modo da tela
# (`desligado` sai sem rodar nem gravar rodada; `silencioso` roda e registra
# sem Threema, decidido dentro do sweep), serialização por advisory lock
# dentro do serviço, log em info só quando a rodada mexeu em algo.
#
# O `import` do serviço é TARDIO (dentro do tick) e tolera ausência: o worker
# tem que subir mesmo se um dos módulos não veio no deploy — sem isso um robô
# faltando derrubaria o import do worker e com ele TODOS os crons da casa.


async def vigia_credenciais_tick(ctx: dict) -> None:
    """Vigia de credenciais (robô da Ouvidoria): conta de marketplace/Bling
    que perdeu o acesso à API (token vencido, chave do app expirada, 403 de
    escopo) → ocorrência `conta:<id>`, e autorização perto de vencer →
    `vence:<id>`. Enquanto uma conta está sem acesso, NENHUM robô da casa
    enxerga aquela loja — por isso é o primeiro da hora.
    """
    try:
        async with session_scope() as s:
            modo = await ouvidoria_modo(s, "vigia_credenciais")
        if modo == "desligado":
            logger.debug("vigia_credenciais_desligado")
            return
        try:
            from app.services.vigia_credenciais import vigia_credenciais_sweep
        except ImportError:
            logger.warning("vigia_credenciais_sem_servico")
            return
        summary = await vigia_credenciais_sweep() or {}
        if any(
            summary.get(k)
            for k in ("novas", "sumiram", "sem_acesso", "vencendo", "avisadas")
        ):
            logger.info("vigia_credenciais_done", **summary)
        else:
            logger.debug("vigia_credenciais_noop", **summary)
    except Exception:  # noqa: BLE001
        logger.exception("vigia_credenciais_unhandled")


async def vigia_ingest_bling_tick(ctx: dict) -> None:
    """Pedido do Bling que não entra (robô da Ouvidoria): webhook de pedido
    que falhou em TODAS as tentativas (3 do arq + as do sweep de 5 min) e cujo
    pedido continua fora de `bling_orders` — ninguém o vê na Margem, na NF nem
    na Logística. Fecha sozinho quando o pedido entra.
    """
    try:
        async with session_scope() as s:
            modo = await ouvidoria_modo(s, "vigia_ingest_bling")
        if modo == "desligado":
            logger.debug("vigia_ingest_bling_desligado")
            return
        try:
            from app.services.vigia_ingest_bling import vigia_ingest_bling_sweep
        except ImportError:
            logger.warning("vigia_ingest_bling_sem_servico")
            return
        summary = await vigia_ingest_bling_sweep() or {}
        if any(
            summary.get(k) for k in ("novas", "sumiram", "esgotados", "avisadas")
        ):
            logger.info("vigia_ingest_bling_done", **summary)
        else:
            logger.debug("vigia_ingest_bling_noop", **summary)
    except Exception:  # noqa: BLE001
        logger.exception("vigia_ingest_bling_unhandled")


async def vigia_correios_tick(ctx: dict) -> None:
    """Ocorrência grave nos Correios (robô da Ouvidoria): apreensão fiscal,
    extravio, roubo, avaria ou devolução ao remetente que o rastreio da
    Logística já leu, 17track sem saldo e rastreio recusado. Só banco e Redis
    (o robô não fala com o 17track), 2 min depois do sync de rastreio.
    """
    try:
        async with session_scope() as s:
            modo = await ouvidoria_modo(s, "vigia_correios")
        if modo == "desligado":
            logger.debug("vigia_correios_desligado")
            return
        try:
            from app.services.vigia_correios import vigia_correios_sweep
        except ImportError:
            logger.warning("vigia_correios_sem_servico")
            return
        summary = await vigia_correios_sweep() or {}
        if any(
            summary.get(k)
            for k in ("novas", "sumiram", "sem_saldo", "quarentena", "avisadas")
        ):
            logger.info("vigia_correios_done", **summary)
        else:
            logger.debug("vigia_correios_noop", **summary)
    except Exception:  # noqa: BLE001
        logger.exception("vigia_correios_unhandled")


async def vigia_marketing_comandos_tick(ctx: dict) -> None:
    """Comandos de Ads não aplicados (robô da Ouvidoria): pausar/retomar,
    orçamento ou Oferta Relâmpago que o executor do Mac não aplicou (falhou,
    ficou pendente ou travou em `claimed`) e executor sem sinal — enquanto
    isso a agenda da Shopee simplesmente não acontece.
    """
    try:
        async with session_scope() as s:
            modo = await ouvidoria_modo(s, "vigia_marketing_comandos")
        if modo == "desligado":
            logger.debug("vigia_marketing_comandos_desligado")
            return
        try:
            from app.services.vigia_marketing_comandos import (
                vigia_marketing_comandos_sweep,
            )
        except ImportError:
            logger.warning("vigia_marketing_comandos_sem_servico")
            return
        summary = await vigia_marketing_comandos_sweep() or {}
        if any(
            summary.get(k)
            for k in (
                "novas", "sumiram", "comandos_falhos", "agendas_falhas",
                "comandos_presos", "avisadas",
            )
        ):
            logger.info("vigia_marketing_comandos_done", **summary)
        else:
            logger.debug("vigia_marketing_comandos_noop", **summary)
    except Exception:  # noqa: BLE001
        logger.exception("vigia_marketing_comandos_unhandled")


async def vigia_robo_melhorenvio_tick(ctx: dict) -> None:
    """Vigia Robô Melhor Envio (robô da Ouvidoria): o executor do Mac Santiago
    que faz o "Suspender entrega" sem sinal, AdsPower fechado, suspensão parada
    na fila ou que falhou com o pacote ainda a caminho."""
    try:
        async with session_scope() as s:
            modo = await ouvidoria_modo(s, "vigia_robo_melhorenvio")
        if modo == "desligado":
            logger.debug("vigia_robo_melhorenvio_desligado")
            return
        try:
            from app.services.vigia_robo_melhorenvio import (
                vigia_robo_melhorenvio_sweep,
            )
        except ImportError:
            logger.warning("vigia_robo_melhorenvio_sem_servico")
            return
        summary = await vigia_robo_melhorenvio_sweep() or {}
        if any(
            summary.get(k)
            for k in (
                "novas", "sumiram", "suspensoes_pendentes", "suspensoes_presas",
                "suspensoes_falhas", "avisadas",
            )
        ):
            logger.info("vigia_robo_melhorenvio_done", **summary)
        else:
            logger.debug("vigia_robo_melhorenvio_noop", **summary)
    except Exception:  # noqa: BLE001
        logger.exception("vigia_robo_melhorenvio_unhandled")


async def vigia_robo_leitura_tick(ctx: dict) -> None:
    """Vigia Robô Leitura de Chamados (robô da Ouvidoria): o executor do Mac
    Santiago que lê o "Histórico da Solicitação" da Shopee sem sinal, ou
    devolução da fila dele que ficou sem leitura."""
    try:
        async with session_scope() as s:
            modo = await ouvidoria_modo(s, "vigia_robo_leitura")
        if modo == "desligado":
            logger.debug("vigia_robo_leitura_desligado")
            return
        from app.services.vigia_robo_leitura import vigia_robo_leitura_sweep

        summary = await vigia_robo_leitura_sweep() or {}
        if any(summary.get(k) for k in ("novas", "sumiram", "casos_sem_leitura", "avisadas")):
            logger.info("vigia_robo_leitura_done", **summary)
        else:
            logger.debug("vigia_robo_leitura_noop", **summary)
    except Exception:  # noqa: BLE001
        logger.exception("vigia_robo_leitura_unhandled")


async def vigia_margem_tick(ctx: dict) -> None:
    """Robô da Margem (robô da Ouvidoria): pedido que o robô segurou no Bling
    e ninguém decidiu, falha do robô ao segurar/liberar (aberta por hook no
    próprio margem_auto_hold) e margem fora do normal. Roda em :17/:47, 2 min
    DEPOIS do ciclo das :15/:45 (`verificar_margem_snapshot`: rebuild do
    snapshot + `margem_auto_hold.run`) — é o snapshot daquele ciclo que a
    rodada lê, e é o hold dele que abre a `falha:` que ela confere.
    """
    try:
        async with session_scope() as s:
            modo = await ouvidoria_modo(s, "vigia_margem")
        if modo == "desligado":
            logger.debug("vigia_margem_desligado")
            return
        try:
            from app.services.vigia_margem import vigia_margem_sweep
        except ImportError:
            logger.warning("vigia_margem_sem_servico")
            return
        summary = await vigia_margem_sweep() or {}
        if any(
            summary.get(k)
            for k in (
                "novas", "sumiram", "segurados_novos", "margem_alta_novas",
                "falhas_abertas", "avisadas",
            )
        ):
            logger.info("vigia_margem_done", **summary)
        else:
            logger.debug("vigia_margem_noop", **summary)
    except Exception:  # noqa: BLE001
        logger.exception("vigia_margem_unhandled")


async def vigia_chamados_tick(ctx: dict) -> None:
    """Chamados: réplica e monitoramento (robô da Ouvidoria): réplica/abertura
    que não foi pra plataforma (aberta por hook no ponto do envio), caso que a
    consulta não consegue mais ler e chamado Encerrado esperando alguém
    concluir pelo Resolver.
    """
    try:
        async with session_scope() as s:
            modo = await ouvidoria_modo(s, "vigia_chamados")
        if modo == "desligado":
            logger.debug("vigia_chamados_desligado")
            return
        try:
            from app.services.vigia_chamados import vigia_chamados_sweep
        except ImportError:
            logger.warning("vigia_chamados_sem_servico")
            return
        summary = await vigia_chamados_sweep() or {}
        if any(
            summary.get(k)
            for k in (
                "novas", "sumiram", "encerrados", "envios_falhos",
                # 22/09: sem isto a rodada ficaria MUDA justamente quando há caso
                # de tela sem ninguém lendo e nada mais acontecendo — o sinal do
                # silêncio some no debug. O painel mostra, o log também precisa.
                "consultas_falhando", "leitura_parada", "avisadas",
            )
        ):
            logger.info("vigia_chamados_done", **summary)
        else:
            logger.debug("vigia_chamados_noop", **summary)
    except Exception:  # noqa: BLE001
        logger.exception("vigia_chamados_unhandled")


async def nf_recuperar_tick(ctx: dict) -> None:
    """Recuperador de NF: retry de comando failed (teto 3), destrava de lease
    expirado e re-encadeamento de pedidos 'processando' órfãos (varredura
    anti-esquecimento). Mesma flag nf_auto_enfileirar (default DESLIGADO);
    serializado por advisory xact lock próprio dentro do service.
    """
    if not get_settings().nf_auto_enfileirar:
        logger.debug("nf_recuperar_disabled")
        return
    try:
        summary = await run_recuperar_nf()
        if any(summary.get(k) for k in (
            "retry", "esgotados", "destravados",
            "orfaos_faturamento", "orfaos_etiqueta",
        )):
            logger.info("nf_recuperar_done", **summary)
        else:
            logger.debug("nf_recuperar_noop", **summary)
    except Exception:  # noqa: BLE001
        logger.exception("nf_recuperar_unhandled")


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


async def faturas_vencimento_scan(ctx: dict) -> None:
    """Avisa 1 dia antes do vencimento de cada fatura (assinatura recorrente).
    Também pega o próprio dia do vencimento como rede de segurança, caso o
    aviso da véspera tenha sido perdido. Alerta só na tela (sem Telegram).

    O dedupe_key inclui a `data_vencimento` (não a data de hoje): garante um
    único aviso por ciclo. Ao renovar, o admin move a data → a chave muda e o
    próximo aviso dispara no ciclo seguinte. Notifica todos os admins."""
    today = datetime.now(UTC).date()
    tomorrow = today + timedelta(days=1)
    async with session_scope() as s:
        faturas = (
            await s.execute(
                select(Fatura).where(
                    Fatura.data_vencimento >= today,
                    Fatura.data_vencimento <= tomorrow,
                )
            )
        ).scalars().all()
        if not faturas:
            logger.debug("faturas_vencimento_scan_noop")
            return
        admin_ids = (
            await s.execute(select(User.id).where(User.role == UserRole.ADMIN))
        ).scalars().all()
        emitted = 0
        for f in faturas:
            venc = f.data_vencimento
            quando = "vence amanhã" if venc == tomorrow else "vence hoje"
            plano = f" ({f.plano})" if f.plano else ""
            for uid in admin_ids:
                a = await emit_alert(
                    s,
                    user_id=uid,
                    type=AlertType.GENERIC,
                    severity=AlertSeverity.WARNING,
                    title=f"Fatura {quando}: {f.servico}",
                    message=f"{f.servico}{plano} {quando} ({venc.isoformat()}).",
                    payload={
                        "fatura_id": str(f.id),
                        "servico": f.servico,
                        "data_vencimento": venc.isoformat(),
                    },
                    dedupe_key=f"fatura_venc:{f.id}:{venc.isoformat()}",
                    notify_telegram=False,
                )
                if a is not None:
                    emitted += 1
        logger.info(
            "faturas_vencimento_scan_done",
            faturas=len(faturas),
            admins=len(admin_ids),
            emitted=emitted,
        )


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
    6/21/83965 sem em_andamento_data, criados ≤14d, sem update há >15min) e
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


async def bling_orders_daily_backfill_tick(ctx: dict) -> None:
    """1×/dia: lista todos os pedidos do Bling por data de emissão (janela
    curta) e re-ingere via ingest_bling_order_run só os que estão AUSENTES
    do banco (insert) ou com situação divergente (update). Pula os já
    presentes e inalterados — nada de re-insert/re-stamp. É a única rede que
    recupera um pedido cujo ingest falhou e nunca entrou.
    Desligável via ENABLE_BLING_ORDERS_DAILY_BACKFILL=false."""
    if not get_settings().enable_bling_orders_daily_backfill:
        return

    from app.services.bling_orders_daily_backfill import (
        find_daily_backfill_candidates,
    )

    async with session_scope() as session:
        bling_ids, user_id = await find_daily_backfill_candidates(session)

    if not bling_ids or user_id is None:
        logger.info("bling_orders_daily_backfill_no_candidates")
        return

    pool = await get_arq_pool()
    enqueued = 0
    for bling_id in bling_ids:
        try:
            await pool.enqueue_job(
                "ingest_bling_order_run",
                bling_id,
                str(user_id),
                "daily_backfill",
            )
            enqueued += 1
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "bling_daily_backfill_enqueue_failed",
                bling_id=bling_id, err=str(e)[:200],
            )

    logger.info(
        "bling_orders_daily_backfill_tick_done",
        candidates=len(bling_ids), enqueued=enqueued,
    )


# Sweep de re-drive dos ingests de pedido que falharam em definitivo.
INGEST_SWEEP_MAX_ATTEMPTS = 8
# Espera curta pós-falha: dá tempo dos 3 retries do arq assentarem antes de o
# sweep entrar. Menor que isso re-enfileiraria por cima do próprio retry.
INGEST_SWEEP_MIN_AGE = timedelta(minutes=3)
# Não ressuscita registro antigo — passou disso, é caso pro backfill diário
# (que casa contra a listagem do Bling e não re-dirige pedido inexistente).
INGEST_SWEEP_MAX_AGE = timedelta(days=3)
INGEST_SWEEP_BATCH = 200


async def ingest_orders_retry_sweep(ctx: dict) -> None:
    """Re-dirige pedidos cujo ingest esgotou os retries do arq.

    Cada webhook de pedido grava um BackgroundJob(type=ingest_bling_order); ao
    falhar em definitivo ele fica FAILED (visível ao failed_jobs_alert_scan).
    Este cron (5 min) re-enfileira esses FAILED — já tem o `bling_id` no payload,
    então NÃO re-lista o Bling — com teto de tentativas (`sweep_attempts`) e uma
    idade mínima que evita competir com o retry do arq. Recuperação em minutos,
    não no backfill diário. Passado o teto, o registro fica FAILED e o backfill
    diário é a última rede. Desligável via ENABLE_INGEST_ORDERS_RETRY_SWEEP=false.
    """
    if not get_settings().enable_ingest_orders_retry_sweep:
        return

    now = datetime.now(UTC)
    ready_before = now - INGEST_SWEEP_MIN_AGE
    floor = now - INGEST_SWEEP_MAX_AGE
    re_enqueued = 0
    exhausted = 0
    async with session_scope() as s:
        rows = (
            await s.execute(
                select(BackgroundJob)
                .where(
                    BackgroundJob.type == BackgroundJobType.INGEST_BLING_ORDER,
                    BackgroundJob.status == BackgroundJobStatus.FAILED,
                    BackgroundJob.finished_at.is_not(None),
                    BackgroundJob.finished_at < ready_before,
                    BackgroundJob.created_at >= floor,
                )
                .order_by(BackgroundJob.finished_at)
                .limit(INGEST_SWEEP_BATCH)
            )
        ).scalars().all()

        pool = await get_arq_pool()
        for job in rows:
            payload = dict(job.payload or {})
            attempts = int(payload.get("sweep_attempts", 0) or 0)
            if attempts >= INGEST_SWEEP_MAX_ATTEMPTS:
                exhausted += 1
                continue
            bling_order_id = payload.get("bling_order_id")
            user_id = payload.get("user_id")
            if bling_order_id is None or user_id is None:
                continue
            event = payload.get("event")
            try:
                arq = await pool.enqueue_job(
                    "ingest_bling_order_run",
                    int(bling_order_id),
                    str(user_id),
                    event,
                    str(job.id),
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "ingest_orders_retry_sweep_enqueue_failed",
                    job_id=str(job.id), err=str(e)[:200],
                )
                continue
            # Só muta o job depois do enqueue OK — senão um PENDING órfão (sem
            # arq job) ficaria preso (o background_jobs_gc só recicla RUNNING).
            payload["sweep_attempts"] = attempts + 1
            job.payload = payload
            job.status = BackgroundJobStatus.PENDING
            job.error = None
            if arq is not None:
                job.arq_job_id = arq.job_id
            re_enqueued += 1
        # session_scope commita as mutações de payload/status.

    if exhausted:
        logger.warning(
            "ingest_orders_retry_sweep_exhausted",
            count=exhausted, cap=INGEST_SWEEP_MAX_ATTEMPTS,
        )
    logger.info(
        "ingest_orders_retry_sweep_done",
        candidates=len(rows), re_enqueued=re_enqueued, exhausted=exhausted,
    )


# ---------------------------------------------------------------- lifecycle


async def certificacoes_anatel_sync(ctx: dict) -> dict:
    """Consulta diária da Makisa, com recuperação após reinício e nova tentativa após erro."""
    from app.services.certificacoes_sync import sincronizar_certificacoes

    async with session_scope() as session:
        result = await sincronizar_certificacoes(session)
    logger.info("certificacoes_anatel_sync", **result)
    return result


async def certificacoes_inmetro_sync(ctx: dict) -> dict:
    """Consulta diária do ProdCert, independente da atualização da Anatel."""
    from app.services.certificacoes_inmetro_sync import sincronizar_certificacoes

    async with session_scope() as session:
        result = await sincronizar_certificacoes(session)
    logger.info("certificacoes_inmetro_sync", **result)
    return result


async def startup(ctx: dict) -> None:
    from app.services.sentry import init_sentry
    init_sentry(component="worker")
    # Catálogo da Ouvidoria (ouvidoria_robos): robô novo no código ganha a
    # linha antes do 1º tick — rodada e ocorrência têm FK pro robô. Dois
    # workers subindo juntos podem colidir no INSERT; é só log, o outro fez.
    try:
        async with session_scope() as s:
            await ouvidoria_sincronizar_catalogo(s)
    except Exception as e:  # noqa: BLE001 — sem catálogo o worker sobe do mesmo jeito
        logger.warning("ouvidoria_catalogo_startup_falhou", error=str(e)[:200])
    logger.info("worker_startup")


async def shutdown(ctx: dict) -> None:
    logger.info("worker_shutdown")


_FIVE_MIN = {0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55}
_TWO_MIN = set(range(0, 60, 2))
_TEN_MIN = {0, 10, 20, 30, 40, 50}


class WorkerSettings:
    redis_settings = RedisSettings.from_dsn(_settings.arq_redis_url)
    functions = [
        certificacoes_anatel_sync,
        certificacoes_inmetro_sync,
        send_otp_email,
        auth_codes_cleanup,
        auto_link_run,
        chamado_devolucao_disparar,
        devolucao_mensagem_comprador_enviar,
        bling_situacoes_sync,
        # O "Sincronizar Todos" completo (include_all_stock, ~30k links) leva
        # ~25-30 min só de chamadas externas — o job_timeout global de 1800s
        # matava a barra a ~98% (TimeoutError em 2/jul, job 7c1b0d83). 3h de
        # teto: o advisory lock por usuário já impede rodar dois em paralelo.
        func(sync_all_run, timeout=10800),
        sync_product_run,
        ml_backfill_run,
        refresh_bling_stock_run,
        export_notas_run,
        ingest_bling_order_run,
        auto_create_product_from_bling_run,
        create_bling_kit_for_mark_job,
        sync_import_product_to_bling_job,
        push_lote_stock_to_bling_job,
        alerts_cleanup,
        condicao_especial_gc,
        margem_reavaliar_reprovados,
        low_stock_polling,
        import_listings_run,
        auto_import_link,
        auto_import_link_run,
        push_prices_batch_run,
        sync_bling_costs_run,
        audit_run,
        user_relink_run,
        sync_marketplace_financials_for_order_run,
        tiktok_unsettled_sweep,
        tiktok_unsettled_fast_lane,
        verificar_margem_snapshot,
        check_marketplace_shipped_orders,
        bling_orders_safety_net_tick,
        bling_orders_period_sync_tick,
        bling_orders_daily_backfill_tick,
        ingest_orders_retry_sweep,
        bling_notas_token_refresh,
        kit_components_sync,
        valuation_estoque_snapshot,
        valuation_estoque_catchup,
        logistica_ml_ingest,
        logistica_marketplaces_ingest,
        # Escopo normal (pendentes do painel) termina em minutos, mas se a fila
        # de pendências crescer o 1800s global mataria o job de novo — foi
        # exatamente o que aconteceu em 12-13/ago com o escopo antigo.
        func(logistica_sweep_shopee, timeout=1800),
        func(logistica_sweep_tiktok, timeout=1800),
        func(logistica_sweep_ml, timeout=1800),
        func(logistica_sweep_amazon, timeout=1800),
        logistica_vigia,
        nf_auto_enfileirar_tick,
        nf_recuperar_tick,
        prioridade_estoque_tick,
        prioridade_estoque_estorno_tick,
        vigia_importacao_tick,
        vigia_estoque_familia_tick,
        # Os 6 robôs da Ouvidoria de 22/09 (cada um sai na hora se o modo da
        # tela estiver `desligado`).
        vigia_credenciais_tick,
        vigia_ingest_bling_tick,
        vigia_correios_tick,
        vigia_marketing_comandos_tick,
        vigia_margem_tick,
        vigia_chamados_tick,
        vigia_robo_melhorenvio_tick,
        vigia_robo_leitura_tick,
    ]
    cron_jobs = [
        # A consulta bem-sucedida agenda a próxima em 24h; falhas tentam de novo em 1h.
        # Tick horário e startup recuperam consultas perdidas durante reinícios.
        cron(certificacoes_anatel_sync, minute=35, run_at_startup=True, timeout=240),
        cron(certificacoes_inmetro_sync, minute=45, run_at_startup=True, timeout=240),
        cron(auth_codes_cleanup, hour=6, minute=15, run_at_startup=False),
        # 06:00 UTC = 03:00 BRT — quiet window, also the daily-sync mass enqueue trigger.
        cron(daily_sync_scheduler, minute=_FIVE_MIN, run_at_startup=False),
        # Daily refresh de products.bling_cost_price (todos os produtos) via
        # listagem /produtos. Roda a cada 6h (00/06/12/18:50 UTC) + no startup
        # do worker — antes era 1×/dia e run_at_startup=False, então um restart
        # perto das 06:50 fazia o custo ficar preso no valor velho o dia todo, e
        # pedidos novos nasciam com custo defasado. Pedidos snapshotam esse custo
        # (o ingest ainda re-busca on-demand SKUs com custo > 3h via _cost_price_by_sku).
        cron(
            product_bling_cost_sync,
            hour={0, 6, 12, 18},
            minute=50,
            run_at_startup=True,
        ),
        # Semanal: cache da composição dos kits (bling_kit_components). Domingo
        # 04:30 UTC = 01:30 BRT — janela tranquila. Estrutura muda raramente.
        cron(kit_components_sync, weekday="sun", hour=4, minute=30, run_at_startup=False),
        # Diário 11:00 UTC = 08:00 BRT (mesmo horário da rotina externa
        # estoque-bling-diario antes da migração): crawl Bling, grava
        # snapshot do estoque por local em valuation_estoque_bling_diario
        # e atualiza valuation.estoque (total). A aba "Estoque Bling" da
        # página /financeiro/valuation lê dessa tabela.
        cron(valuation_estoque_snapshot, hour=11, minute=0, run_at_startup=False),
        # Rede de segurança do snapshot acima: toda hora (:20) e a cada
        # restart do worker, refaz o crawl SÓ se o dia (SP) ainda não tem
        # linha e já passou das 08:00 BRT. Sem isso, um deploy na janela das
        # 08:00 deixava o dia sem Estoque (02/09, 11/09, 14/09).
        cron(valuation_estoque_catchup, minute=20, run_at_startup=True),
        # Toda hora (:00) — era 1x/dia às 07:00 BRT; pedido do usuário 25/08.
        # Importa os novos pedidos ML pra Logística, realinha status, limpa os
        # finalizados e enriquece o status do Meli (só linhas ainda vazias).
        cron(logistica_ml_ingest, minute=0, run_at_startup=False),
        # Toda hora (:05), junto do ML: novos pedidos Shopee/TikTok/Amazon.
        cron(logistica_marketplaces_ingest, minute=5, run_at_startup=False),
        # Toda hora (:25): réplica automática + acompanhamento dos Chamados
        # (todo chamado de API do ML fecha sozinho quando o claim encerra).
        cron(chamados_replica_automatica, minute=25, run_at_startup=False),
        # A cada 30 min (:10/:40): só reembolso da TikTok → chamado com prazo + Threema.
        cron(chamados_tiktok_reembolso_vigia, minute={10, 40}, run_at_startup=False, timeout=600),
        # 1x/dia (05:50 BRT) e no startup: catálogo de situações = Bling (2 GETs).
        cron(bling_situacoes_sync, hour=8, minute=50, run_at_startup=True, timeout=120),
        # Motor da Logística SOZINHO a cada 5 min (:02, :07... — deslocado dos
        # ingests de :00/:05 pra não estourar rate junto): re-enriquece o Status
        # Plataforma dos pendentes do painel, aplica no Bling as situações com
        # regra e limpa finalizados. É o MESMO job do botão "recarregar" — a
        # trava no redis (dentro do próprio job) impede dois ao mesmo tempo.
        # Varreduras de pós-venda (linhas escondidas da janela de 45 dias): UMA
        # PLATAFORMA POR VEZ, 1×/hora cada, em minutos diferentes — juntas
        # levavam minutos e morriam inteiras em qualquer deploy (15/09). O
        # motor rápido (pendentes do painel) vive no worker de marketplace.
        cron(logistica_sweep_shopee, minute={9}, run_at_startup=False, timeout=1800),
        cron(logistica_sweep_tiktok, minute={19}, run_at_startup=False, timeout=1800),
        cron(logistica_sweep_ml, minute={29}, run_at_startup=False, timeout=1800),
        cron(logistica_sweep_amazon, minute={49}, run_at_startup=False, timeout=1800),
        # Vigia da automação: avisa (Threema + sino) se algum desses jobs parar.
        cron(logistica_vigia, minute={13, 43}, run_at_startup=False, timeout=120),
        # Rastreio do pacote que VOLTA (Acompanhamento de Devoluções), a cada
        # 30 min (:10/:40 — fora dos slots dos ingests e do recarregar).
        cron(devolucao_rastreio_sync, minute={10, 40}, run_at_startup=False, timeout=1500),
        # Prazo de contestação das devoluções (Shopee/TikTok) + aviso Threema,
        # a cada 30 min (:20/:50 — fora dos slots do rastreio de devolução).
        cron(devolucao_prazo_sync, minute={20, 50}, run_at_startup=False, timeout=900),
        # Rastreio Correios do ENVIO (aba Logística), a cada 15 min (:05/:20/
        # :35/:50 — fora dos slots do recarregar e do sync de devolução).
        cron(
            logistica_track_sync,
            minute={5, 20, 35, 50},
            run_at_startup=False,
            timeout=900,
        ),
        # Reconsulta FORÇADA nos Correios via 17track. Começou em 08/09 com
        # 07:00 e 16:30 (escolha do Eduardo); em 10/09 virou DE 3 EM 3 HORAS,
        # depois do pedido 295070: os Correios publicaram "objeto apreendido
        # pela Secretaria da Fazenda" às 10h10, o 17track leu às 10h40 e trouxe
        # informação velha — só a releitura forçada trouxe o evento. Eduardo:
        # "pode ser mais nessas forçadas, precisa sempre estar atualizado".
        # 04/07/10/13/19/22 BRT (07/10/13/16/22/01 UTC) + o 16:31 BRT original.
        # O intervalo de 3 h casa com FRESCO_HORAS=3 do próprio job: quem o
        # 17track já leu nas últimas 3 h é pulado, então rodar mais vezes não
        # multiplica gasto — e o teto diário de re-registros pagos continua.
        cron(
            logistica_track_forcar,
            hour={1, 7, 10, 13, 16, 22},
            minute=1,
            run_at_startup=False,
            timeout=900,
        ),
        cron(logistica_track_forcar, hour=19, minute=31, run_at_startup=False, timeout=900),
        # Amazon Envio próprio: avisos Threema de prazo (08:06 e 16:06 BRT).
        cron(logistica_amazon_avisos, hour={11, 19}, minute=6, run_at_startup=False, timeout=600),
        # Mensagens ao comprador da Amazon a cada 15 min (:08/:23/:38/:53 —
        # depois do sync do 17track de :05/:20/:35/:50, que é quem carimba
        # entrega e ocorrência grave).
        cron(
            logistica_cliente_mensagens,
            minute={8, 23, 38, 53},
            run_at_startup=False,
            timeout=600,
        ),
        cron(bling_token_refresh, minute={15}, run_at_startup=False),
        # Contas de NF (bling_notas): AT dura 6h, refresh a cada 5h. Gaps
        # 5/5/5/5/4h — sempre abaixo da expiração. minute=45 evita colidir
        # com o refresh da integração principal (:15) no mesmo rate slot.
        cron(
            bling_notas_token_refresh,
            hour={0, 5, 10, 15, 20},
            minute=45,
            run_at_startup=False,
        ),
        cron(shopee_token_refresh, hour={0, 4, 8, 12, 16, 20}, minute=0, run_at_startup=False),
        cron(ml_token_refresh, minute={0, 30}, run_at_startup=False),
        cron(tiktok_token_refresh, hour={0, 6, 12, 18}, minute=45, run_at_startup=False),
        # Token das contas de rede social (robô de postagem): a janela de aviso
        # é de 7 dias, então 1×/dia basta. 12:35 UTC = 09:35 BRT — o alerta cai
        # no sino em horário de expediente, com tempo de reconectar a conta.
        cron(meta_token_refresh, hour=12, minute=35, run_at_startup=False),
        cron(marketplace_financials_retry, minute={10, 40}, run_at_startup=False),
        # Conferência do preço vivo na Amazon depois dos envios da Tabela.
        cron(
            pricing_confirmacao_amazon_tick,
            minute={4, 14, 24, 34, 44, 54},
            run_at_startup=True,
            timeout=600,
        ),
        # 10:00 UTC = 07:00 BRT — leitura diária da caixa do Tuta.
        cron(tuta_devolucoes_tick, hour=10, minute=0, run_at_startup=False),
        # Esteira lenta: backlog de falha de API. Fila e teto próprios pra não
        # competir com o retry dos pedidos do dia.
        cron(
            marketplace_financials_esteira_lenta,
            minute={5, 20, 35, 50},
            run_at_startup=False,
            timeout=900,
        ),
        # Reabre a fila do que morreu por queda de API (ver a função).
        cron(
            marketplace_financials_ressuscitar,
            minute={2},
            run_at_startup=True,
            timeout=300,
        ),
        # A cada 10min ("pegar isso estantaneo", 01/09): o TikTok libera a
        # estimativa ~30-60min após a venda (caso 293707, medido ao vivo) e o
        # tick de 30min somava até mais meia hora em cima. 10min × 8 lojas ×
        # ~2 páginas ≈ 16 chamadas/tick — longe do rate limit compartilhado
        # (36009002 foi com centenas de chamadas por-pedido). Minutos {3,13,…}
        # desalinhados do financials_retry (:10/:40) e do snapshot (:15/:45);
        # o job já reconstrói o snapshot sozinho quando acha valor novo.
        cron(tiktok_unsettled_sweep, minute={3, 13, 23, 33, 43, 53}, run_at_startup=False),
        # Todo minuto (campo minute omitido = wildcard): quase sempre é só um
        # SELECT local; API do TikTok apenas quando há pedido <3h sem
        # estimativa — ver a docstring do job.
        cron(tiktok_unsettled_fast_lane, run_at_startup=False),
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
        # Condição Especial de segmento encerrada há 30d (services/condicao_especial).
        cron(condicao_especial_gc, hour=6, minute=10, run_at_startup=False),  # 03:10 BRT
        cron(failed_jobs_alert_scan, minute=_TWO_MIN, run_at_startup=False),
        cron(webhook_signature_alert_scan, minute={5, 35}, run_at_startup=False),
        cron(low_stock_polling, minute=_TWO_MIN, run_at_startup=False),
        cron(faturas_vencimento_scan, hour=11, minute=5, run_at_startup=False),  # 08:05 BRT

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
        # Marketing: schedule reconciler (BRT windows → outbox) every minute.
        # Runs HERE on the always-on central server because it only WRITES to
        # the marketing_commands outbox — no marketplace API call — so it isn't
        # bound by the Shopee/ML partner throttle that pins the consumer to the
        # agent node. It enqueues 'browser' commands (Shopee → local marionete
        # via /agent/lease) and 'api' commands (ML/Amazon → agent-node consumer).
        cron(marketing_reconcile_schedules, run_at_startup=False),
        # Marketing: Oferta Relâmpago (Shopee flash-sale) — enfileira a
        # duplicação diária às 01:00 BRT = 04:00 UTC (o worker roda em UTC).
        # Igual ao reconciler, só ESCREVE no outbox (comandos 'browser' pra
        # marionete local via /agent/lease), então roda aqui no central.
        cron(marketing_flash_duplicate, hour=4, minute=0, run_at_startup=False),
        # Robô de postagem dos criativos (Marketing × Redes Sociais). Roda no
        # central: quem publica é o SERVIDOR, pela Graph API oficial — nada
        # disso passa pelo executor do Mac (que existe só porque a API de Ads
        # da Shopee é bloqueada). Mac desligado às 19h = post agendado perdido.
        # A agenda vira fila a cada minuto…
        cron(marketing_postagens_promover, run_at_startup=False),
        # …e o publicador também roda a cada minuto. `timeout=1200` com folga
        # sobre o teto do upload (600s) + o poll da transcodificação (300s):
        # o job precisa TERMINAR por conta própria, porque um kill do arq no
        # meio da chamada que publica deixa a postagem sem resposta. O próprio
        # laço para em `_ORCAMENTO_TICK` e devolve o que não deu tempo.
        # Dois ticks se sobreporem é inofensivo: cada um reserva linhas
        # diferentes (SKIP LOCKED no `proximas_para_publicar`).
        cron(marketing_postagens_publicar, run_at_startup=False, timeout=1200),
        # Resposta de DM: mesmo tick de 1 minuto, pelo mesmo motivo —
        # mensagem é reativa e o cliente está esperando agora.
        cron(dm_responder_pendentes, run_at_startup=False, timeout=300),
        # Reconciliação a cada 10 min, no :05 (longe do congestionamento do
        # :00): postagem presa é CONSULTADA, nunca retentada.
        cron(
            marketing_postagens_reconciliar,
            minute={5, 15, 25, 35, 45, 55},
            run_at_startup=False,
            timeout=300,
        ),
        # Métricas dos vídeos publicados (24/09/2026). A da noite, às 23:47
        # BRT (02:47 UTC), lê tudo: o retrato do dia D fica sendo o número do
        # fim de D, e "views ganhas por dia" cai no dia certo. Nas outras 23
        # horas, no mesmo :47, o passe curto lê o vídeo que ainda não tem
        # número (vídeo novo ganha número em até 1 hora, não 16) e retenta a
        # falha recente — reler o resto de hora em hora não traz informação.
        # `timeout=1800` na da noite: uma pausa de 1 s entre páginas do TikTok
        # e até 600 posts. A trava no Redis põe uma rodada por vez.
        # `max_tries=7`: travada, a da noite levanta Retry(defer=300) — e o
        # `cron()` do arq tem max_tries=1 por padrão (o 3 do WorkerSettings
        # não vale pra cron), então o Retry morria sem rodar e o dia ficava
        # sem o retrato do fim. 6 retentativas de 5 min = 30 min, o TTL da
        # trava: nem trava órfã (worker morto no deploy) fica sem a noite.
        cron(
            marketing_postagens_metricas,
            hour={_metricas.HORA_NOTURNA_UTC},
            minute={_metricas.MINUTO_COLETA},
            run_at_startup=False,
            timeout=1800,
            max_tries=7,
        ),
        cron(
            marketing_postagens_metricas_recentes,
            hour=set(range(24)) - {_metricas.HORA_NOTURNA_UTC},
            minute={_metricas.MINUTO_COLETA},
            run_at_startup=False,
            timeout=600,
        ),
        # Publicação autônoma: de hora em hora, no minuto 2. A grade é horária
        # (18h, 19h…), então rodar mais vezes não adianta — e cada passada
        # varre os criativos de todas as marcas ligadas.
        cron(marketing_autopostagem, minute={2}, run_at_startup=False, timeout=300),
        # Marketing: Shopee round-robin MOVED to the agent-node block below —
        # only the dedicated machine (MARKETING_AGENT_NODE=1) talks to Shopee
        # Ads, so the central server never competes on the same partner-id
        # throttle (which would rate-limit both).
        # Snapshot de margem: backstop de rebuild_all a cada 30 min (:15/:45,
        # fora do pico de crons em :00). RE-ADICIONADO porque o refresh
        # per-ingest agora pula re-syncs (bling_orders._DEFER_MARGEM_REFRESH_
        # EVENTS) — sem este cron, a mudança de status de um pedido re-sincado
        # só chegaria ao snapshot quando alguém abrisse a página /margem
        # (que também reconstrói, throttle 5min). Este cron garante a
        # propagação em períodos ociosos. Serializado pelo advisory lock.
        cron(verificar_margem_snapshot, minute={15, 45}, run_at_startup=False),
        # Reavaliação dos reprovados pelo robô: de hora em hora, em :35 — fora
        # do :15/:45 (snapshot + hold), do :10/:40 (retry do financeiro) e do
        # :20 (period sync). Rebusca financeiro + Bling por pedido; poucos
        # candidatos (≈10 reprovações automáticas/dia). Ver margem_auto_hold.
        cron(margem_reavaliar_reprovados, minute=35, run_at_startup=False, timeout=600),
        # Cron `check_marketplace_shipped_orders` MOVIDO pra
        # WorkerSettingsMarketplace (fila `davinci_marketplace`). Função
        # continua em `functions` deste worker como fallback (enqueue
        # manual via /admin/run-job pega aqui também). Em pico, cron
        # disputava fila com webhooks de marketplace e atrasava 15-20
        # min mesmo configurado a cada 5.
        # Safety-net: re-sincroniza pedidos suspeitos de stale (webhooks
        # perdidos do Bling) a cada 10 min. Refetch via ingest_bling_order_run.
        cron(bling_orders_safety_net_tick, minute=_TEN_MIN, run_at_startup=False),
        # Varredura por período: de hora em hora (:20) lista pedidos
        # alterados nas últimas 2h (dataAlteracao) e re-ingere. Pega webhooks
        # perdidos de QUALQUER transição (situação-agnóstica), inclusive as
        # que a safety-net não cobre (ex.: 15 → devolução).
        cron(bling_orders_period_sync_tick, minute={20}, run_at_startup=False),
        # Varredura DIÁRIA por data de emissão (09:30 UTC = 06:30 BRT): lista
        # todos os pedidos do dia no Bling e ingere os AUSENTES do banco
        # (insert) ou com situação divergente (update). Pula presentes e
        # inalterados. Única rede que recupera pedido cujo ingest falhou e
        # nunca entrou (Bling 500 / transação envenenada).
        cron(bling_orders_daily_backfill_tick, hour=9, minute=30, run_at_startup=False),
        # Re-drive (5 min) dos ingests de pedido que esgotaram os retries do
        # arq: re-enfileira os BackgroundJob(ingest_bling_order) FAILED pelo
        # bling_id do payload (sem re-listar o Bling), com teto de tentativas.
        # Recuperação em minutos em vez de esperar o backfill diário.
        cron(ingest_orders_retry_sweep, minute=_FIVE_MIN, run_at_startup=False),
        # Auto-enfileirador de NF: varre Shopee/TikTok "Em aberto" a cada
        # 2 min, confere estoque e enfileira a importação avulsa sozinho
        # (fundindo o backlog pending num arquivo só por faturador).
        # Inerte enquanto NF_AUTO_ENFILEIRAR não estiver true no .env.
        cron(nf_auto_enfileirar_tick, minute=_TWO_MIN, run_at_startup=False),
        # Recuperador de NF: retry de failed (teto 3 tentativas), destrava de
        # lease preso (>45min) e re-encadeamento de 'processando' órfão. Mesma
        # flag NF_AUTO_ENFILEIRAR — inerte enquanto não estiver true no .env.
        cron(nf_recuperar_tick, minute=_FIVE_MIN, run_at_startup=False),
        # Prioridade de estoque (coluna Prioridade da Tabela de Preços):
        # troca o SKU do pedido "Em aberto" pra tag prioritária logo que ele
        # cai — antes de margem/NF. Minutos ÍMPARES = a cada 2 min (Eduardo
        # 28/08: "nao da pra diminuir esse tempo de 10 min"; o espelho chega
        # via webhook em segundos, então o cron era o gargalo), fora de fase
        # dos crons pesados dos minutos pares. Tick sem candidato = 2 SELECTs
        # leves e ZERO chamadas ao Bling. SEM flag (coluna vazia = no-op); os
        # ganchos do enfileirar (auto e manual) cobrem a hora da NF de
        # qualquer jeito.
        cron(prioridade_estoque_tick, minute=set(range(1, 60, 2)), run_at_startup=False),
        # Manutenção das compensações de kit (retry, estorno de cancelado,
        # aviso): 2×/hora em :16/:46, minutos livres. Sem pendência = SELECTs.
        cron(prioridade_estoque_estorno_tick, minute={16, 46}, run_at_startup=False),
        # Vigia de importação (robô da Ouvidoria: pedido pago no ML/Shopee/
        # TikTok/Amazon que não caiu no Bling → ocorrência + Threema). 1×/hora
        # em :09 (minuto livre) — Vinicius, 22/09: de 30 em 30 min era mais
        # do que a operação precisa; `cadencia_texto` do robô descreve isto.
        cron(vigia_importacao_tick, minute=9, run_at_startup=False),
        # Os outros 6 robôs da Ouvidoria (aprovados em 22/09). Minutos fora de
        # fase dos crons pesados e, quando o robô lê o que outro job acabou de
        # gravar, DEPOIS dele: credenciais 1×/h em :21 (uma chamada barata por
        # conta); pedido do Bling que não entra a cada 15 min; Correios 2 min
        # depois do logistica_track_sync (:05/:20/:35/:50); comandos de Ads a
        # cada 10 min em :06… (só banco, e fora dos minutos do espelho de NF-e
        # abaixo, que é :04…); Margem em :17/:47, 2 min DEPOIS do ciclo das
        # :15/:45 que reconstrói o snapshot e aplica o hold (é esse snapshot
        # que a rodada lê, e o hold é quem abre a `falha:`); Chamados em
        # :27/:57, 2 min depois da réplica automática (:25). `cadencia_texto`
        # de cada RoboDef descreve estes minutos pra tela — mexer aqui é
        # mexer lá.
        cron(vigia_credenciais_tick, minute=21, run_at_startup=False),
        cron(vigia_ingest_bling_tick, minute={3, 18, 33, 48}, run_at_startup=False),
        cron(vigia_correios_tick, minute={7, 22, 37, 52}, run_at_startup=False),
        cron(
            vigia_marketing_comandos_tick,
            minute={6, 16, 26, 36, 46, 56},
            run_at_startup=False,
        ),
        cron(vigia_margem_tick, minute={17, 47}, run_at_startup=False),
        cron(vigia_chamados_tick, minute={27, 57}, run_at_startup=False),
        # Robô do Melhor Envio: a cada 10 min em :09… (só banco).
        cron(
            vigia_robo_melhorenvio_tick,
            minute={9, 19, 29, 39, 49, 59},
            run_at_startup=False,
        ),
        # Robô de leitura de chamados: a cada 10 min em :03… (só banco).
        cron(
            vigia_robo_leitura_tick,
            minute={3, 13, 23, 33, 43, 53},
            run_at_startup=False,
        ),
        # Espelho das NF-e das contas de emissão (página Pós Vendas). As
        # contas bling_notas são apps OAuth próprios — rate independente do
        # app principal; o custo por rodada é 1-2 páginas de lista por conta
        # + até 80 detalhes (teto no service).
        cron(pos_vendas_notas_sync, minute={4, 14, 24, 34, 44, 54}, run_at_startup=False),
    ]
    # Marketing agent-node crons (Shopee sync + command consumer + schedule
    # reconciler) are NOT registered here — they run ONLY on the dedicated
    # machine via `WorkerSettingsMarketingAgent` below, which the central
    # server never launches. That's what keeps a single machine on the
    # Shopee partner-id throttle. (marketing_full_sync = ML/Amazon stays here.)
    # Mantido em 10 (conservador). O gargalo real do ingest não é a
    # concorrência e sim o refresh do snapshot verificar_margem, agora
    # SERIALIZADO por advisory lock (verificar_margem._REFRESH_LOCK_KEY) — subir
    # max_jobs só empilharia conexões esperando o lock. O financeiro real saiu
    # pra fila/worker dedicados (WorkerSettingsFinancials), então estes 10 slots
    # ficam só com ingest + crons + sync_product_run.
    # 22/09: os 6 robôs da Ouvidoria entraram aqui. Cada sweep deles segura
    # DUAS conexões enquanto roda (uma sessão só pro advisory lock, porque a de
    # trabalho commita, + a de trabalho), e os minutos acima foram escolhidos
    # pra no máximo um deles cair em cada minuto. Se mais robôs entrarem, é
    # este par de conexões por sweep que tem que ser contado antes de mexer no
    # max_jobs.
    max_jobs = 10
    job_timeout = 1800
    keep_result = 3600
    max_tries = 3
    retry_jobs = True
    on_startup = startup
    on_shutdown = shutdown


class WorkerSettingsUI:
    """Worker dedicado pra jobs disparados pela UI — baixa latência,
    apenas 2 funções (criar kit no Bling + enviar produto pro Bling).
    Roda em paralelo ao WorkerSettings (fila default) — assim clicks
    do operador não esperam pelos 17+ min de backlog de webhooks.

    Sem cron_jobs (todos os crons rodam no worker default)."""

    redis_settings = RedisSettings.from_dsn(_settings.arq_redis_url)
    functions = [
        sync_import_product_to_bling_job,
        create_bling_kit_for_mark_job,
        # Lote de importação grande (40+ items) leva ~2-3 min com o rate
        # limit do Bling (~3 req/s) — o job_timeout global de 60s matou os
        # pushes dos lotes ML27/ML28 em 3/ago (TimeoutError aos 21 items).
        func(push_lote_stock_to_bling_job, timeout=1800),
        # "Atualizar agora" da tela Desempenho (24/09/2026). Aqui e não na
        # default porque a default pode estar horas atrás dos webhooks, e o
        # clique espera a resposta em minutos. `timeout=600` porque o 60s da
        # fila mataria a leitura: são até 120 posts, com 1 s entre páginas do
        # TikTok.
        func(marketing_postagens_metricas_agora, timeout=600),
    ]
    queue_name = ARQ_UI_QUEUE
    # Concorrência baixa — jobs UI são curtos (1-2 chamadas Bling) e
    # vêm em rajadas pequenas. 5 paralelos cobre pico sem encher rate
    # limit do Bling.
    max_jobs = 5
    # 60s é mais que suficiente — POST + PUT + supplier_link no Bling
    # leva ~5s no happy path. Erro/timeout no Bling vai pro retry.
    job_timeout = 60
    keep_result = 3600
    max_tries = 3
    retry_jobs = True
    on_startup = startup
    on_shutdown = shutdown


class WorkerSettingsMarketplace:
    """Worker dedicado pro cron `check_marketplace_shipped_orders`
    (sweeps de 21/83965 → 15 quando marketplace confirma envio). Antes
    rodava no default e disputava com 100+ webhooks/min em pico —
    tick atrasava 15-20 min mesmo configurado a cada 5.

    Sweeps `bling_orders` em etiqueta enviada (21; 83965 legado) contra
    o estado de envio do marketplace. Marketplace SHIPPED → Bling 15 +
    stamp em_andamento_data → pedido sai da fila do estoque.
    """

    redis_settings = RedisSettings.from_dsn(_settings.arq_redis_url)
    functions = [
        check_marketplace_shipped_orders,
        # Motor rápido da Logística (pendentes do painel + regras da aba
        # Status). Saiu do worker default em 15/09: lá ficava na fila atrás dos
        # syncs financeiros (10 slots) e morria em cada deploy — a tela só
        # atualizava quando alguém clicava. 15 min de timeout por função (o
        # global de 120s é curto pra 100 linhas × 4 marketplaces).
        func(logistica_recarregar, timeout=900),
    ]
    cron_jobs = [
        # `run_at_startup=True`: o worker é recriado a cada deploy (~25×/dia) e
        # sem isso a tela ficava até 5 min sem atualizar depois de publicar.
        # A trava no redis impede dois motores ao mesmo tempo.
        cron(
            logistica_recarregar,
            minute={2, 7, 12, 17, 22, 27, 32, 37, 42, 47, 52, 57},
            run_at_startup=True,
            timeout=900,
        ),
        # A CADA MINUTO (era a cada 5). O operador reclamava que o pedido
        # já aparecia enviado no marketplace e só entrava no Controle de
        # Estoque "muito tempo depois": os 5 min de cron eram o piso, e o
        # sweep leva ~15-25s pra 200 candidatos (Shopee/TikTok em lote de
        # 50, ML/Amazon com 6 consultas em paralelo). Overlap de ticks é
        # barrado pelo advisory lock em marketplace_shipment_check.
        cron(check_marketplace_shipped_orders, run_at_startup=False),
    ]
    queue_name = ARQ_MARKETPLACE_QUEUE
    # Concorrência baixa — 1 tick por minuto, serializado pelo advisory
    # lock (`_SWEEP_LOCK_KEY`): quem chega e acha o lock tomado sai na
    # hora. 3 slots cobrem o overrun de um tick lento sem enfileirar.
    max_jobs = 3
    # 2 min cobre consulta a N marketplaces; mais que isso indica
    # marketplace fora do ar, melhor abortar e re-tentar no próximo tick.
    job_timeout = 120
    keep_result = 3600
    max_tries = 3
    retry_jobs = True
    on_startup = startup
    on_shutdown = shutdown


class WorkerSettingsFinancials:
    """Worker dedicado pro financeiro real por pedido
    (`sync_marketplace_financials_for_order_run`).

    Antes esse job morava na fila default junto com `ingest_bling_order_run`,
    no mesmo worker (max_jobs=10). Cada busca de financeiro bate em Shopee/ML
    (~25s, sujeita a 429) e é enfileirada 1× por pedido; em pico ela afogava o
    ingest e o backlog da default explodia — 29/06 a fila chegou a ~15k jobs /
    17h de atraso e pedidos novos sumiram da Margem. Fila própria
    (`davinci_financials`) isola os dois: o ingest (que a Margem precisa) drena
    livre na default; o financeiro real chega quando chegar (a taxa estimada
    do Bling é o fallback até lá).

    Sem cron_jobs (todos os crons rodam no worker default)."""

    redis_settings = RedisSettings.from_dsn(_settings.arq_redis_url)
    functions = [
        sync_marketplace_financials_for_order_run,
    ]
    queue_name = ARQ_FINANCIALS_QUEUE
    # 3 → 8: o refresh pesado saiu do caminho do bulk (marketplace_financials
    # pula o _verificar_margem_refresh_silent quando trigger != 'manual'), então
    # o lock não é mais o teto — a vazão passa a ser limitada pela latência da
    # API do marketplace. 8 paralelos drenam a fila ~3× mais rápido sem estourar
    # o rate limit (lookup por pedido, não Ads). Cabe folgado no pool de 30 e na
    # CPU (core liberado do build órfão do Nuxt).
    max_jobs = 8
    job_timeout = 1800
    keep_result = 3600
    max_tries = 3
    retry_jobs = True
    on_startup = startup
    on_shutdown = shutdown


class WorkerSettingsSync:
    """Worker dedicado pro SYNC EM MASSA — `sync_all_run` (Sincronizar Todos),
    `auto_link_run` (Vincular Automático), `sync_product_run` (webhook de
    produto) e `refresh_bling_stock_run`.

    Antes esses 4 jobs moravam na fila default junto do `ingest_bling_order_run`
    (webhook de pedido) e ~25 crons (`WorkerSettings`, max_jobs=10). Um
    Sincronizar Todos completo (~30 min) segurava um slot da default e competia
    com o ingest, e o auto_link processava as ~17 integrações em série — uma
    conta lenta travava a barra. Fila própria (`davinci_sync`) isola o massa: o
    ingest/crons drenam livres na default; o massa roda em paralelo aqui.

    Sem cron_jobs (o `daily_sync_scheduler` mora no default e enfileira o
    `sync_all_run` NESTA fila via get_arq_sync_pool)."""

    redis_settings = RedisSettings.from_dsn(_settings.arq_redis_url)
    functions = [
        # Mantém o teto de 3h do sync_all (Sincronizar Todos completo ~30 min;
        # ver nota em WorkerSettings). O advisory lock por usuário já impede
        # dois em paralelo.
        func(sync_all_run, timeout=10800),
        sync_product_run,
        auto_link_run,
        refresh_bling_stock_run,
    ]
    queue_name = ARQ_SYNC_QUEUE
    # Pool do processo = 30 (pool_size 10 + overflow 20). Pior caso realista:
    # 1 massa segurando um slot (sync_all = 1 + SYNC_ALL_CONCURRENCY(8) sub-
    # sessões = 9 conexões; auto_link = 1 + AUTOLINK_CONCURRENCY(4) = 5, e é
    # mutuamente exclusivo com o sync_all pelo lock) + 7 sync_product/refresh
    # (1 conexão cada) = ~16 < 30. max_jobs=8 cobre rajada de sync_product com
    # folga sem estourar o pool.
    max_jobs = 8
    # Teto por job da classe. O sync_all sobrescreve p/ 10800 via func(); os
    # demais (auto_link/sync_product/refresh) terminam bem abaixo de 1800.
    job_timeout = 1800
    keep_result = 3600
    max_tries = 3
    retry_jobs = True
    on_startup = startup
    on_shutdown = shutdown


class WorkerSettingsMarketingAgent:
    """Worker da MÁQUINA DEDICADA (MARKETING_AGENT_NODE=1) — a ÚNICA que fala
    com Shopee/ML Ads, contornando o rate-limit por partner-id.

    Roda os crons de marketing que EXIGEM a máquina dedicada (não os ~25 do
    WorkerSettings), pra NÃO duplicar refresh de token, daily_sync, safety-net
    etc. do servidor:
      - marketing_shopee_tick:        puxa dados de Ads da Shopee (round-robin)
      - marketing_consume_commands:   drena a fila (comandos executor='api')

    O `marketing_reconcile_schedules` NÃO roda aqui: ele só escreve na outbox
    (sem chamar API), então mora no WorkerSettings central (sempre ligado, é
    quem serve o /agent/lease pro marionete). Deixá-lo aqui também duplicaria o
    trabalho a cada minuto.

    Usa o `arq_redis_url` do ambiente — na máquina dedicada aponte-o pra um
    Redis LOCAL próprio (ex.: redis://localhost:6380/1) pra isolar totalmente
    a fila arq desta máquina da do servidor. O servidor NUNCA inicia esta
    classe (o compose dele roda só WorkerSettings/UI/Marketplace)."""

    redis_settings = RedisSettings.from_dsn(_settings.arq_redis_url)
    functions = [
        marketing_shopee_tick,
        marketing_consume_commands,
    ]
    cron_jobs = [
        cron(
            marketing_shopee_tick,
            minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55},
            run_at_startup=False,
        ),
        cron(marketing_consume_commands, second={0, 20, 40}, run_at_startup=False),
    ]
    queue_name = "davinci_marketing"
    # Baixa concorrência: o consumidor processa lotes pequenos e o sync Shopee
    # é serializado pelo throttle. 4 cobre folga.
    max_jobs = 4
    # Sync de uma loja Shopee pode levar minutos (delay de 30s entre chamadas
    # × várias chamadas). 600s cobre com folga.
    job_timeout = 600
    keep_result = 3600
    max_tries = 3
    retry_jobs = True
    on_startup = startup
    on_shutdown = shutdown


class WorkerSettingsMarketingApi:
    """Cloud-side executor for ML/Amazon ('api') ad commands.

    Runs ONLY `marketing_consume_commands`: drains the marketing_commands outbox
    for executor='api' rows and applies pause/resume/budget against the ML Ads
    API — reachable from the cloud, unlike Shopee whose Ads API is blocked (that
    path uses the external marionete via /agent/lease). Deliberately split from
    WorkerSettingsMarketingAgent so this cloud node does NOT also run
    `marketing_shopee_tick` (which pulls Shopee Ads data and only works from the
    dedicated machine).

    Runtime-gated by MARKETING_AGENT_NODE=1 (re-checked inside
    marketing_consume_commands); set that flag on THIS service only, so exactly
    one node consumes the 'api' outbox."""

    redis_settings = RedisSettings.from_dsn(_settings.arq_redis_url)
    functions = [marketing_consume_commands]
    cron_jobs = [
        cron(marketing_consume_commands, second={0, 20, 40}, run_at_startup=False),
    ]
    queue_name = "davinci_marketing_api"
    max_jobs = 4
    job_timeout = 120
    keep_result = 3600
    max_tries = 3
    retry_jobs = True
    on_startup = startup
    on_shutdown = shutdown


# Re-export for tests / introspection
__all__ = [
    "WorkerSettings",
    "WorkerSettingsMarketingAgent",
    "WorkerSettingsMarketingApi",
    "WorkerSettingsMarketplace",
    "WorkerSettingsSync",
    "WorkerSettingsUI",
    "audit_run",
    "auth_codes_cleanup",
    "auto_import_link",
    "auto_link_run",
    "alerts_cleanup",
    "condicao_especial_gc",
    "margem_reavaliar_reprovados",
    "background_jobs_gc",
    "bling_notas_token_refresh",
    "bling_orders_safety_net_tick",
    "bling_orders_period_sync_tick",
    "bling_token_refresh",
    "daily_sync_scheduler",
    "failed_jobs_alert_scan",
    "import_listings_run",
    "ingest_bling_order_run",
    "ingest_orders_retry_sweep",
    "kit_components_sync",
    "low_stock_polling",
    "product_bling_cost_sync",
    "ml_backfill_run",
    "ml_token_refresh",
    "marketplace_financials_retry",
    "pricing_confirmacao_amazon_tick",
    "tuta_devolucoes_tick",
    "marketplace_financials_esteira_lenta",
    "marketplace_financials_ressuscitar",
    "tiktok_unsettled_sweep",
    "tiktok_unsettled_fast_lane",
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
