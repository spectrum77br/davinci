"""Token refresh lock — prevents concurrent OAuth token refresh per integration.

When multiple webhook handlers or sync jobs trigger a 401 simultaneously,
they all try to refresh the token at once. With Bling (which rotates
refresh_token on every use), this causes:
  1. First refresh succeeds, gets new RT
  2. Second refresh uses the OLD RT (already invalidated) → permanent lockout

This module provides a Redis-based distributed lock so only ONE refresh
attempt per integration can proceed at a time. Others wait for the result.

Usage:
    async with token_refresh_lock(integration_id) as acquired:
        if acquired:
            await client.refresh()
        else:
            # Another process is refreshing — wait and use updated creds
            await asyncio.sleep(2)
            # Re-read creds from DB
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from uuid import UUID

import structlog

from app.redis_client import redis

logger = structlog.get_logger()

# Lock TTL: if a refresh takes longer than this, the lock auto-releases
# to prevent deadlocks. Normal refresh takes < 5s.
REFRESH_LOCK_TTL_SECONDS = 30

# How long to wait for another process's refresh to complete
REFRESH_WAIT_TIMEOUT_SECONDS = 15


def _lock_key(integration_id: UUID | str) -> str:
    return f"token_refresh_lock:{integration_id}"


@asynccontextmanager
async def token_refresh_lock(integration_id: UUID | str):
    """Try to acquire a refresh lock for the given integration.

    Yields True if lock acquired (caller should proceed with refresh).
    Yields False if another process holds the lock (caller should wait
    and re-read credentials from DB).
    """
    key = _lock_key(integration_id)
    # Try to acquire with NX (only if not exists) + EX (auto-expire)
    acquired = await redis.set(key, "1", nx=True, ex=REFRESH_LOCK_TTL_SECONDS)

    if acquired:
        try:
            yield True
        finally:
            # Release the lock
            await redis.delete(key)
    else:
        # Lock held by another process — wait for it to complete
        logger.info(
            "token_refresh_lock_waiting",
            integration_id=str(integration_id),
        )
        yield False


async def wait_for_refresh(integration_id: UUID | str) -> bool:
    """Wait until the refresh lock is released (another process finished).

    Returns True if lock was released within timeout, False if timed out.
    """
    key = _lock_key(integration_id)
    elapsed = 0.0
    interval = 0.5

    while elapsed < REFRESH_WAIT_TIMEOUT_SECONDS:
        exists = await redis.exists(key)
        if not exists:
            return True
        await asyncio.sleep(interval)
        elapsed += interval

    logger.warning(
        "token_refresh_lock_timeout",
        integration_id=str(integration_id),
        timeout=REFRESH_WAIT_TIMEOUT_SECONDS,
    )
    return False
