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
    PricingAccount,
    Segment,
    User,
    UserSettings,
)
from app.services.pricing.push import push_one
from app.services.telegram import TelegramClient, TelegramConfigError

logger = structlog.get_logger()


def _now() -> datetime:
    return datetime.now(UTC)


_DEPT_LABEL = {
    "celular": "Celular",
    "mala": "Mala",
    "eletro": "Eletro",
    "catalogo": "Catálogo ML",
}


async def _infer_batch_department(
    session: AsyncSession, items: list[dict]
) -> str | None:
    """Pick the department slug of the first item's pricing_account. The
    bulk-push UI is per-department so this is normally unambiguous; mixed
    batches just get the first one's label."""
    if not items:
        return None
    try:
        acc_id = UUID(items[0]["pricing_account_id"])
    except (KeyError, ValueError):
        return None
    acc = await session.get(PricingAccount, acc_id)
    if acc is None or acc.segment_id is None:
        return None
    seg = await session.get(Segment, acc.segment_id)
    if seg is None:
        return None
    if seg.parent_id is None:
        return _DEPT_LABEL.get(seg.slug, seg.slug)
    parent = await session.get(Segment, seg.parent_id)
    if parent is None:
        return None
    return _DEPT_LABEL.get(parent.slug, parent.slug)


async def _resolve_chat_id(session: AsyncSession, user_id: UUID) -> str | None:
    settings = (
        await session.execute(
            select(UserSettings).where(UserSettings.user_id == user_id)
        )
    ).scalar_one_or_none()
    return settings.telegram_chat_id if settings else None


def _format_report(summary: dict, samples: list[dict], department: str | None = None) -> str:
    """SSH-style Telegram report. Categorizes failures into
    "Pulados (encerrados)", "Sem vínculo", and "Erros" so the user can
    triage at a glance and see which SKUs need attention."""
    closed_codes = {"ml_listing_closed", "ml_listing_paused", "ml_listing_under_review"}
    no_link_codes = {"no_link", "listing_not_found"}

    skipped = []
    no_link = []
    errors = []
    for s in samples:
        code = s.get("code") or ""
        if code in closed_codes:
            skipped.append(s)
        elif code in no_link_codes:
            no_link.append(s)
        elif code not in ("ok", "partial"):
            errors.append(s)

    header_dept = f" — {department}" if department else ""
    lines = [
        f"🔔 <b>Relatório de Envio de Preços{header_dept}</b>",
        f"✅ Enviados: {summary['ok']}",
        f"⏭️ Pulados (encerrados): {len(skipped)}",
        f"❌ Erros: {len(errors)}",
        f"🔗 Sem vínculo: {len(no_link)}",
    ]

    def _fmt(s: dict) -> str:
        price = f" R$ {s['price']}" if s.get("price") else ""
        detail = s.get("detail") or ""
        if len(detail) > 80:
            detail = detail[:77] + "…"
        return f"• <code>{s.get('code','')}</code>{price} {detail}".rstrip()

    if errors:
        lines.append("")
        lines.append(f"<b>Erros (até 10 de {len(errors)}):</b>")
        for s in errors[:10]:
            lines.append(_fmt(s))
    if no_link:
        lines.append("")
        lines.append(f"<b>Sem vínculo (até 10 de {len(no_link)}):</b>")
        for s in no_link[:10]:
            lines.append(_fmt(s))
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
                department_label = await _infer_batch_department(session, items)
                await TelegramClient(default_chat_id=chat_id).safe_send(
                    _format_report(summary, failures, department_label)
                )
            except TelegramConfigError:
                logger.warning("batch_push_telegram_skipped", reason="no_token")
