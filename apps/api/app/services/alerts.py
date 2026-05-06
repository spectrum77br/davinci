"""Alert emission helpers.

Emit alerts with optional `dedupe_key` — a partial UNIQUE on
`(user_id, dedupe_key) WHERE dedupe_key IS NOT NULL` collapses repeat events
(e.g. same product crossing low-stock threshold every 2min).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Alert, AlertSeverity, AlertType


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
        return a

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
    return (
        await session.execute(select(Alert).where(Alert.id == row[0]))
    ).scalar_one_or_none()
