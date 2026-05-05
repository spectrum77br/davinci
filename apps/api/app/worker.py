from datetime import UTC, datetime, timedelta

import structlog
from arq import cron
from arq.connections import RedisSettings
from sqlalchemy import delete

from app.config import get_settings
from app.db import session_scope
from app.models import AuthCode
from app.services.email import get_email_sender, render_otp_html

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


async def startup(ctx: dict) -> None:
    logger.info("worker_startup")


async def shutdown(ctx: dict) -> None:
    logger.info("worker_shutdown")


class WorkerSettings:
    redis_settings = RedisSettings.from_dsn(_settings.arq_redis_url)
    functions = [send_otp_email, auth_codes_cleanup]
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
