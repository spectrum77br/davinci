"""Off-hot-path storage for background-job progress entries.

`background_jobs.details` (a JSONB array on the job row) turned quadratic under
`sync_all`: every append rewrote the whole array under a row lock, and the 8
parallel sub-orchestrators all hammered the same row, serializing on the lock.
Each progress entry now lands as one cheap INSERT into `background_job_details`,
so appends are O(1), lock-free between tasks, and the read API can page the tail
by `id` instead of shipping the whole array on every poll.

The schema is resolved from settings (not a hardcoded `davinci.`) so the same
path works against the `davinci_test` schema the test suite pins via
`DATABASE_SCHEMA`. The schema name is a trusted config value, never user input —
hence the `# noqa: S608` on the interpolated statements (same pattern the test
conftest uses for its `DELETE FROM {tbl}` wipes).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings


def _schema() -> str:
    return get_settings().database_schema


def _as_dict(value: Any) -> dict:
    """asyncpg hands JSONB back as a `str` when the column type isn't known to
    SQLAlchemy (raw `text()` queries). Parse defensively so callers always get
    a dict regardless of driver codec."""
    if isinstance(value, str):
        return json.loads(value)
    return value


async def append_job_detail(
    session: AsyncSession, job_id: UUID, entry: dict[str, Any]
) -> None:
    """Append one progress entry for `job_id`.

    A single-row INSERT — no JSONB rewrite, no row-lock on the job row, so the
    parallel sub-orchestrators don't serialize. Does NOT commit: the caller
    batches the commit with its heartbeat/progress flush so we never pay a
    round-trip per link.
    """
    if "at" not in entry:
        entry = {"at": datetime.now(UTC).isoformat(), **entry}
    schema = _schema()
    sql = (
        f'INSERT INTO "{schema}".background_job_details (job_id, entry) '  # noqa: S608
        "VALUES (:jid, CAST(:entry AS jsonb))"
    )
    await session.execute(text(sql), {"jid": str(job_id), "entry": json.dumps(entry)})


async def count_job_details(session: AsyncSession, job_id: UUID) -> int:
    """Number of progress entries recorded for `job_id` (cheap, covered by the
    (job_id, id) index). Powers `details_count` on the light /status endpoint."""
    schema = _schema()
    sql = (
        f'SELECT COUNT(*) FROM "{schema}".background_job_details '  # noqa: S608
        "WHERE job_id = :jid"
    )
    return int(
        (await session.execute(text(sql), {"jid": str(job_id)})).scalar_one()
    )


async def load_job_details(
    session: AsyncSession,
    job_id: UUID,
    *,
    after_id: int = 0,
    limit: int = 500,
) -> tuple[list[dict], int]:
    """Return up to `limit` entries for `job_id` with child-id > `after_id`, in
    insertion order, plus the max child-id in the slice (or `after_id` if the
    slice is empty). Powers the incremental /details poll's cursor."""
    schema = _schema()
    sql = (
        f'SELECT id, entry FROM "{schema}".background_job_details '  # noqa: S608
        "WHERE job_id = :jid AND id > :after ORDER BY id ASC LIMIT :lim"
    )
    rows = (
        await session.execute(
            text(sql), {"jid": str(job_id), "after": after_id, "lim": limit}
        )
    ).all()
    items = [_as_dict(r.entry) for r in rows]
    max_id = rows[-1].id if rows else after_id
    return items, max_id


async def load_job_details_tail(
    session: AsyncSession, job_id: UUID, *, limit: int = 500
) -> list[dict]:
    """Last `limit` entries in insertion order — for one-shot responses (the
    synchronous single-product sync) that want the whole, small log at once."""
    schema = _schema()
    sql = (
        f'SELECT entry FROM (SELECT id, entry FROM "{schema}".background_job_details '  # noqa: S608, E501
        "WHERE job_id = :jid ORDER BY id DESC LIMIT :lim) t ORDER BY id ASC"
    )
    rows = (
        await session.execute(text(sql), {"jid": str(job_id), "lim": limit})
    ).all()
    return [_as_dict(r.entry) for r in rows]
