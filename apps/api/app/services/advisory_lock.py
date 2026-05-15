"""Postgres advisory locks scoped to a SyncOrchestrator run.

Replaces the legacy `syncLock.ts` mechanism (PRD §11 Fase 4a).

Usage:

    async with try_user_sync_lock(session, user_id) as acquired:
        if not acquired:
            raise HTTPException(409, "sync_already_running")
        ...

Locks are session-scoped: released when the underlying connection is checked
back into the pool. Use `session_scope()` from `app.db` to control lifetime.
"""

from __future__ import annotations

import hashlib
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Namespace constant: first int passed to `pg_try_advisory_lock(int4, int4)`.
# Picked arbitrarily; uniqueness inside this app is enough to avoid collisions
# with other advisory locks the DB might use.
SYNC_NAMESPACE: int = 0x53594E43  # ascii "SYNC"


def _user_lock_key(user_id: UUID) -> int:
    """Project a UUID into a signed int4 deterministically."""
    h = hashlib.blake2b(user_id.bytes, digest_size=4).digest()
    val = int.from_bytes(h, "big", signed=False)
    # Map to int4 signed range Postgres expects.
    if val >= 2**31:
        val -= 2**32
    return val


@asynccontextmanager
async def try_user_sync_lock(session: AsyncSession, user_id: UUID):
    """Try to acquire a non-blocking advisory lock for `user_id`'s sync run.

    Yields True if acquired (caller proceeds and releases on exit), False if
    another process already holds it (caller should 409).
    """
    key = _user_lock_key(user_id)
    row = await session.execute(
        text("SELECT pg_try_advisory_lock(:ns, :k)"),
        {"ns": SYNC_NAMESPACE, "k": key},
    )
    acquired: bool = bool(row.scalar())
    try:
        yield acquired
    finally:
        if acquired:
            await session.execute(
                text("SELECT pg_advisory_unlock(:ns, :k)"),
                {"ns": SYNC_NAMESPACE, "k": key},
            )


async def release_stale_sync_locks(
    session: AsyncSession, *, idle_minutes: int = 30
) -> list[int]:
    """Terminate backends that hold our SYNC_NAMESPACE advisory lock and have
    been idle for at least `idle_minutes`. Returns the PIDs that were killed.

    Counterpart to SSH's 30-min in-memory safety timeout. A killed task or a
    pool connection that didn't run pg_advisory_unlock leaves the lock held;
    without this cleanup the next sync_all raises sync_already_running until
    the connection is recycled (which can be hours).
    """
    rows = (
        await session.execute(
            text(
                """
                SELECT l.pid
                FROM pg_locks l
                JOIN pg_stat_activity a ON a.pid = l.pid
                WHERE l.locktype = 'advisory'
                  AND l.classid = :ns
                  AND a.state = 'idle'
                  AND (now() - a.query_start) > make_interval(mins => :mins)
                """
            ),
            {"ns": SYNC_NAMESPACE, "mins": idle_minutes},
        )
    ).all()
    killed: list[int] = []
    for row in rows:
        pid = row.pid
        try:
            r = await session.execute(
                text("SELECT pg_terminate_backend(:pid)"), {"pid": pid}
            )
            if r.scalar():
                killed.append(pid)
        except Exception:  # noqa: BLE001
            continue
    return killed
