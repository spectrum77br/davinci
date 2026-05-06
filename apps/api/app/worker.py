from datetime import UTC, datetime, timedelta
from uuid import UUID

import structlog
from arq import cron
from arq.connections import RedisSettings
from sqlalchemy import delete

from sqlalchemy import and_, select

from app.config import get_settings
from app.db import session_scope
from app.models import (
    AuthCode,
    BackgroundJob,
    BackgroundJobStatus,
    Product,
)
from app.services.advisory_lock import try_user_sync_lock
from app.services.auto_link import run_auto_link
from app.services.email import get_email_sender, render_otp_html
from app.services.sync_orchestrator import SyncOrchestrator

logger = structlog.get_logger()
_settings = get_settings()


async def send_otp_email(ctx: dict, *, email: str, prefix: str, code: str, ttl_minutes: int) -> None:
    sender = get_email_sender()
    html = render_otp_html(prefix=prefix, code=code, ttl_minutes=ttl_minutes)
    text = (
        f"DaVinci\n\n"
        f"Confirme o prefixo: {prefix}\n"
        f"Código: {code}\n"
        f"Expira em {ttl_minutes} minutos.\n"
    )
    await sender.send(
        to=email,
        subject=f"DaVinci — Código {prefix}",
        html=html,
        text=text,
    )


async def auth_codes_cleanup(ctx: dict) -> None:
    cutoff = datetime.now(UTC) - timedelta(days=7)
    async with session_scope() as s:
        result = await s.execute(delete(AuthCode).where(AuthCode.expires_at < cutoff))
        logger.info("auth_codes_cleanup_done", deleted=result.rowcount or 0)


async def auto_link_run(
    ctx: dict,
    job_id: str,
    user_id: str,
    integration_ids: list[str] | None,
) -> None:
    async with session_scope() as s:
        await run_auto_link(
            s,
            job_id=UUID(job_id),
            user_id=UUID(user_id),
            integration_ids=[UUID(i) for i in (integration_ids or [])] or None,
        )


async def sync_all_run(
    ctx: dict,
    job_id: str,
    user_id: str,
    product_ids: list[str] | None,
) -> None:
    """Fase 4a: full sync run. Acquires per-user advisory lock; if busy, marks
    job as `failed` with `error='sync_already_running'`."""
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

            where = [Product.user_id == uid]
            if product_ids:
                where.append(Product.id.in_([UUID(p) for p in product_ids]))
            products = (
                await s.execute(select(Product).where(and_(*where)))
            ).scalars().all()
            job.total = len(products)
            await s.commit()

            orch = SyncOrchestrator(s, user_id=uid, job=job)
            await orch.run(products)


async def startup(ctx: dict) -> None:
    logger.info("worker_startup")


async def shutdown(ctx: dict) -> None:
    logger.info("worker_shutdown")


class WorkerSettings:
    redis_settings = RedisSettings.from_dsn(_settings.arq_redis_url)
    functions = [send_otp_email, auth_codes_cleanup, auto_link_run, sync_all_run]
    cron_jobs = [
        cron(
            auth_codes_cleanup,
            hour=6,
            minute=15,
            run_at_startup=False,
        ),
    ]
    max_jobs = 10
    job_timeout = 1800
    keep_result = 3600
    max_tries = 3
    retry_jobs = True
    on_startup = startup
    on_shutdown = shutdown
