"""Sync metrics — Phase 13 (observability).

Tracks per-marketplace counters and latencies in Redis so the API and worker
processes share state. The `/api/metrics` admin endpoint reads this snapshot.

Storage layout (all under `MET:` prefix):
- HASH `MET:syncs:{platform}`: fields `total`, `ok`, `skipped`, `retryable`,
  `fatal`, `requires_review`, `latency_sum_ms`, `latency_count`.
- ZSET `MET:errors:{platform}`: member=error_code, score=count.

All operations are best-effort: a Redis outage degrades to logs only — never
blocks a sync. The recorder is module-level so callers do not need DI plumbing.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from time import monotonic

import structlog

from app.redis_client import redis

logger = structlog.get_logger()

_PLATFORMS = ("bling", "ml", "shopee", "amazon", "tiktok", "temu", "aliexpress")
_STATUSES = ("ok", "skipped", "retryable", "fatal", "requires_review")
_PREFIX_SYNC = "MET:syncs"
_PREFIX_ERR = "MET:errors"


def _platform_key(platform: str) -> str:
    return f"{_PREFIX_SYNC}:{platform}"


def _errors_key(platform: str) -> str:
    return f"{_PREFIX_ERR}:{platform}"


async def record_sync(
    platform: str,
    status: str,
    *,
    latency_ms: float,
    error_code: str | None = None,
) -> None:
    """Increment the per-platform counters. Safe to call from anywhere; never
    raises — Redis errors are logged at WARNING and swallowed."""
    try:
        pipe = redis.pipeline()
        key = _platform_key(platform)
        pipe.hincrby(key, "total", 1)
        pipe.hincrby(key, status, 1)
        pipe.hincrbyfloat(key, "latency_sum_ms", float(latency_ms))
        pipe.hincrby(key, "latency_count", 1)
        if error_code:
            pipe.zincrby(_errors_key(platform), 1, error_code)
        await pipe.execute()
    except Exception as e:  # noqa: BLE001
        logger.warning("metrics_record_failed", platform=platform, err=str(e))


@asynccontextmanager
async def time_sync(platform: str):
    """Context manager that records a metric on exit. Use:

        async with time_sync('ml') as bucket:
            ...
            bucket.set('ok')
            bucket.error('ml_put_item_status_429')
    """
    started = monotonic()
    bucket = _Bucket()
    try:
        yield bucket
    finally:
        latency_ms = (monotonic() - started) * 1000.0
        if bucket.status:
            await record_sync(
                platform,
                bucket.status,
                latency_ms=latency_ms,
                error_code=bucket.error_code,
            )


class _Bucket:
    __slots__ = ("status", "error_code")

    def __init__(self) -> None:
        self.status: str | None = None
        self.error_code: str | None = None

    def set(self, status: str) -> None:
        self.status = status

    def error(self, code: str | None) -> None:
        self.error_code = code


async def snapshot() -> dict:
    """Read aggregated metrics for every platform. Returns a JSON-serializable
    dict: per-platform counts + average latency + top error codes."""
    out: dict = {"platforms": {}}
    try:
        for p in _PLATFORMS:
            raw = await redis.hgetall(_platform_key(p))
            if not raw:
                continue
            total = int(raw.get("total", 0))
            counts = {s: int(raw.get(s, 0)) for s in _STATUSES}
            latency_sum = float(raw.get("latency_sum_ms", 0.0))
            latency_count = int(raw.get("latency_count", 0))
            avg_ms = (latency_sum / latency_count) if latency_count else 0.0
            errors_raw = await redis.zrevrange(
                _errors_key(p), 0, 9, withscores=True
            )
            errors = [{"code": code, "count": int(score)} for code, score in errors_raw]
            out["platforms"][p] = {
                "total": total,
                **counts,
                "avg_latency_ms": round(avg_ms, 2),
                "samples": latency_count,
                "top_errors": errors,
            }
    except Exception as e:  # noqa: BLE001
        logger.warning("metrics_snapshot_failed", err=str(e))
        out["error"] = "metrics_unavailable"
    return out


async def reset() -> None:
    """Wipe every metric key — admin-triggered or used by tests."""
    try:
        for p in _PLATFORMS:
            await redis.delete(_platform_key(p), _errors_key(p))
    except Exception as e:  # noqa: BLE001
        logger.warning("metrics_reset_failed", err=str(e))
