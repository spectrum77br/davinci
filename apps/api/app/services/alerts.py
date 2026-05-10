"""Alert emission helpers.

Emit alerts with optional `dedupe_key` — a partial UNIQUE on
`(user_id, dedupe_key) WHERE dedupe_key IS NOT NULL` collapses repeat events
(e.g. same product crossing low-stock threshold every 2min).

By default, every emitted alert is also pushed to Telegram when the user has
`user_settings.notify_telegram` on. Callers that already format their own
Telegram message (richer body / custom header) should pass
`notify_telegram=False` to avoid double-send.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Alert, AlertSeverity, AlertType, UserSettings

logger = structlog.get_logger()

_SEVERITY_PREFIX = {
    AlertSeverity.ERROR: "🔴",
    AlertSeverity.WARNING: "🟠",
    AlertSeverity.SUCCESS: "🟢",
    AlertSeverity.INFO: "🔵",
}


async def emit_alert(
    session: AsyncSession,
    *,
    user_id: UUID,
    type: AlertType,
    title: str,
    severity: AlertSeverity = AlertSeverity.INFO,
    message: str | None = None,
    payload: dict[str, Any] | None = None,
    dedupe_key: str | None = None,
    notify_telegram: bool = True,
) -> Alert | None:
    """Insert an alert. With `dedupe_key`, conflicts are silently ignored
    (returns None when the dedupe row already exists)."""
    values = {
        "user_id": user_id,
        "type": type.value,
        "severity": severity.value,
        "title": title,
        "message": message,
        "payload": payload or {},
        "dedupe_key": dedupe_key,
    }
    if dedupe_key is None:
        a = Alert(
            user_id=user_id,
            type=type,
            severity=severity,
            title=title,
            message=message,
            payload=payload or {},
        )
        session.add(a)
        await session.flush()
    else:
        stmt = (
            pg_insert(Alert.__table__)
            .values(**values)
            .on_conflict_do_nothing(
                index_elements=["user_id", "dedupe_key"],
                index_where=Alert.__table__.c.dedupe_key.is_not(None),
            )
            .returning(Alert.__table__.c.id)
        )
        result = await session.execute(stmt)
        row = result.first()
        if row is None:
            return None
        a = (
            await session.execute(select(Alert).where(Alert.id == row[0]))
        ).scalar_one_or_none()
        if a is None:
            return None

    if notify_telegram:
        await _push_telegram(session, user_id=user_id, alert=a)
    return a


async def _push_telegram(
    session: AsyncSession, *, user_id: UUID, alert: Alert
) -> None:
    """Best-effort Telegram dispatch. Honors `user_settings.notify_telegram`."""
    try:
        us = await session.get(UserSettings, user_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("alert_telegram_lookup_failed", err=str(e))
        return
    if us is None or not us.notify_telegram:
        return

    from app.services.telegram import TelegramClient

    prefix = _SEVERITY_PREFIX.get(alert.severity, "")
    head = f"{prefix} <b>{alert.title}</b>".strip()
    body = head if not alert.message else f"{head}\n{alert.message}"
    tg = TelegramClient()
    await tg.safe_send(body, chat_id=us.telegram_chat_id)
