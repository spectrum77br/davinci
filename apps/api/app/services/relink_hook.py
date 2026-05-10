"""Debounced enqueue of the per-user relink job.

Replaces the 30-min `auto_import_link` cron tick with on-demand triggers
fired from the write paths that can produce new product↔listing matches:
- product create/patch/bulk-import in `routers/products.py`
- listing patch (SKU change) in `routers/listings.py`
- `run_import_listings` already runs the linker inline at end of its job

A Redis `SETNX` with short TTL collapses bursty triggers (e.g. bulk Bling
import that touches 500 products in one HTTP request) into a single
worker run.
"""
from __future__ import annotations

from uuid import UUID

import structlog

from app.redis_client import redis
from app.worker_pool import get_arq_pool

logger = structlog.get_logger()

DEBOUNCE_TTL = 10  # seconds — long enough to absorb bulk writes
DEBOUNCE_PREFIX = "relink:debounce:"


async def trigger_user_relink(user_id: UUID) -> bool:
    """Enqueue `user_relink_run` for `user_id` unless a debounce key is set.

    Returns True if a fresh enqueue happened, False if collapsed into a
    pending one. Fails open: if Redis is unreachable we still enqueue.
    """
    key = f"{DEBOUNCE_PREFIX}{user_id}"
    try:
        acquired = await redis.set(key, "1", nx=True, ex=DEBOUNCE_TTL)
    except Exception as e:  # noqa: BLE001
        logger.warning("relink_debounce_redis_failed", err=str(e))
        acquired = True
    if not acquired:
        return False
    try:
        pool = await get_arq_pool()
        await pool.enqueue_job("user_relink_run", str(user_id))
    except Exception as e:  # noqa: BLE001
        logger.warning("relink_enqueue_failed", user_id=str(user_id), err=str(e))
        return False
    return True
