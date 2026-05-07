"""Bulk pricing push runner (Fase 9c).

Runs in an Arq job so the user can fire 100s of cells without keeping the
HTTP request open. Drives `push_one` sequentially (respects per-platform
rate limits already inside MLClient._request) and writes progress to
`background_jobs`. On completion, fires a Telegram report via `safe_send`
when the user has Telegram configured (or the global chat is set).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BackgroundJob,
    BackgroundJobStatus,
    User,
    UserSettings,
)
from app.services.pricing.push import push_one
from app.services.telegram import TelegramClient, TelegramConfigError

logger = structlog.get_logger()


def _now() -> datetime:
    return datetime.now(UTC)


async def _resolve_chat_id(session: AsyncSession, user_id: UUID) -> str | None:
    settings = (
        await session.execute(
            select(UserSettings).where(UserSettings.user_id == user_id)
        )
    ).scalar_one_or_none()
    return settings.telegram_chat_id if settings else None


def _format_report(summary: dict, samples: list[dict]) -> str:
    lines = [
        "<b>Push de preços — relatório</b>",
        f"Total: {summary['total']}",
        f"OK: {summary['ok']}  ·  Falhas: {summary['failed']}  ·  Cached: {summary['cached']}",
    ]
    if samples:
        lines.append("")
        lines.append("<b>Falhas (até 10):</b>")
        for s in samples[:10]:
            price = f" R$ {s['price']}" if s.get("price") else ""
            lines.append(
                f"• <code>{s['code']}</code>{price} — {s.get('detail') or ''}"
            )
    return "\n".join(lines)


async def run_push_prices_batch(
    session: AsyncSession,
    *,
    job_id: UUID,
    user_id: UUID,
    items: list[dict],
    idempotency_prefix: str | None = None,
    notify_telegram: bool = True,
) -> None:
    """Idempotency model: if `idempotency_prefix` is set, each item gets
    `f"{prefix}:{i}"` as its key — a re-enqueue with the same prefix replays
    the cached responses without extra marketplace traffic (B13 across the
    whole batch)."""
    job = await session.get(BackgroundJob, job_id)
    if job is None:
        logger.error("batch_job_missing", job_id=str(job_id))
        return

    job.status = BackgroundJobStatus.RUNNING
    job.started_at = _now()
    job.last_heartbeat_at = _now()
    job.total = len(items)
    job.processed = 0
    await session.commit()

    user = await session.get(User, user_id)
    if user is None:
        job.status = BackgroundJobStatus.FAILED
        job.error = "user_not_found"
        job.finished_at = _now()
        await session.commit()
        return

    summary = {"total": len(items), "ok": 0, "failed": 0, "cached": 0}
    failures: list[dict] = []
    details: list[dict] = []

    for i, item in enumerate(items):
        try:
            account_id = UUID(item["pricing_account_id"])
            product_id = UUID(item["pricing_product_id"])
        except (KeyError, ValueError) as e:
            summary["failed"] += 1
            failures.append({"code": "bad_input", "detail": str(e)})
            job.processed = i + 1
            continue

        key = f"{idempotency_prefix}:{i}" if idempotency_prefix else None
        outcome = await push_one(
            session,
            user=user,
            account_id=account_id,
            product_id=product_id,
            idempotency_key=key,
        )
        if outcome.ok:
            summary["ok"] += 1
        else:
            summary["failed"] += 1
            failures.append(outcome.to_dict())
        if outcome.cached:
            summary["cached"] += 1
        details.append(outcome.to_dict())

        job.processed = i + 1
        if job.processed % 10 == 0:
            job.last_heartbeat_at = _now()
            await session.commit()

    job.status = BackgroundJobStatus.SUCCEEDED
    job.result = {"summary": summary, "details": details[-50:]}
    job.finished_at = _now()
    await session.commit()

    if notify_telegram:
        chat_id = await _resolve_chat_id(session, user_id)
        if chat_id:
            try:
                await TelegramClient(default_chat_id=chat_id).safe_send(
                    _format_report(summary, failures)
                )
            except TelegramConfigError:
                logger.warning("batch_push_telegram_skipped", reason="no_token")
