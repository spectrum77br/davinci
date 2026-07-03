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
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, select, text
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
    """Try to acquire a non-blocking *transactional* advisory lock for the
    user's sync run.

    Yields True if acquired (caller proceeds), False if another process
    holds it (caller should 409).

    Uses `pg_try_advisory_xact_lock` rather than the session-level variant
    so the lock is released automatically when the surrounding transaction
    commits OR rolls back. The session-level lock has a subtle leak: if the
    transaction aborts before `pg_advisory_unlock` runs, the unlock call
    itself fails (`current transaction is aborted`) and the lock stays
    pinned to the pooled connection — every subsequent sync sees
    `sync_already_running` until the connection is recycled.
    """
    key = _user_lock_key(user_id)
    row = await session.execute(
        text("SELECT pg_try_advisory_xact_lock(:ns, :k)"),
        {"ns": SYNC_NAMESPACE, "k": key},
    )
    acquired: bool = bool(row.scalar())
    yield acquired


# Janela p/ considerar um massa "ativo". O sync_all completo roda até ~3h
# (WorkerSettings sync_all timeout=10800). Um RUNNING órfão é coberto pelo
# background_jobs_gc (5min sem heartbeat → FAILED); esta janela só limita um
# PENDING que nunca foi pego pelo worker (raro) pra não travar o gate p/ sempre.
_MASS_SYNC_ACTIVE_WINDOW = timedelta(hours=4)


async def mass_sync_active(session: AsyncSession, user_id: UUID) -> dict | None:
    """Retorna o massa em andamento do usuário, ou None.

    "Massa" = Sincronizar Todos (SYNC_ALL) ou Vincular Automático (AUTO_LINK).
    Gate determinístico de exclusividade "só outro massa": os endpoints de
    enqueue recusam (409) se já existe um; o sync individual por SKU NÃO usa
    este gate (continua livre). Preferido ao advisory lock porque o lock é
    transacional e só é adquirido DENTRO do worker (há uma janela entre o
    enqueue e o pickup em que ninguém o segura) — uma linha PENDING/RUNNING é
    visível imediatamente após o enqueue.
    """
    # Import local: app.models importa cedo demais p/ topo deste módulo.
    from app.models import BackgroundJob, BackgroundJobStatus, BackgroundJobType

    cutoff = datetime.now(UTC) - _MASS_SYNC_ACTIVE_WINDOW
    row = (
        await session.execute(
            select(BackgroundJob)
            .where(
                and_(
                    BackgroundJob.created_by == user_id,
                    BackgroundJob.type.in_(
                        [BackgroundJobType.SYNC_ALL, BackgroundJobType.AUTO_LINK]
                    ),
                    BackgroundJob.status.in_(
                        [BackgroundJobStatus.PENDING, BackgroundJobStatus.RUNNING]
                    ),
                    BackgroundJob.finished_at.is_(None),
                    BackgroundJob.created_at >= cutoff,
                )
            )
            .order_by(BackgroundJob.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    return {"job_id": str(row.id), "type": row.type.value, "status": row.status.value}


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
