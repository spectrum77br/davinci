"""Phase 13 — metrics service tests."""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.redis_client import redis
from app.services import metrics


@pytest_asyncio.fixture(autouse=True)
async def _clean_metrics():
    await metrics.reset()
    yield
    await metrics.reset()


@pytest.mark.asyncio
async def test_record_sync_increments_counters() -> None:
    await metrics.record_sync("ml", "ok", latency_ms=120.0)
    await metrics.record_sync("ml", "ok", latency_ms=80.0)
    await metrics.record_sync(
        "ml", "fatal", latency_ms=200.0, error_code="ml_put_item_status_500"
    )

    snap = await metrics.snapshot()
    ml = snap["platforms"]["ml"]
    assert ml["total"] == 3
    assert ml["ok"] == 2
    assert ml["fatal"] == 1
    assert ml["samples"] == 3
    assert ml["avg_latency_ms"] == pytest.approx((120 + 80 + 200) / 3, rel=1e-3)
    assert ml["top_errors"][0] == {"code": "ml_put_item_status_500", "count": 1}


@pytest.mark.asyncio
async def test_time_sync_context_records_status_and_error() -> None:
    async with metrics.time_sync("shopee") as bucket:
        bucket.set("retryable")
        bucket.error("shopee_429")

    snap = await metrics.snapshot()
    sh = snap["platforms"]["shopee"]
    assert sh["total"] == 1
    assert sh["retryable"] == 1
    assert sh["samples"] == 1
    assert sh["top_errors"][0]["code"] == "shopee_429"


@pytest.mark.asyncio
async def test_time_sync_skips_when_status_not_set() -> None:
    """If caller forgets to call .set(), nothing gets recorded — keeps the
    bucket honest about partial paths."""
    async with metrics.time_sync("amazon"):
        pass

    snap = await metrics.snapshot()
    assert "amazon" not in snap["platforms"]


@pytest.mark.asyncio
async def test_metrics_endpoint_admin_only(
    client: AsyncClient, make_user, auth_as
) -> None:
    user = await make_user()
    auth_as(user)
    r = await client.get("/api/metrics")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_metrics_endpoint_returns_snapshot(
    client: AsyncClient, make_user, auth_as
) -> None:
    from app.models import UserRole
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)

    await metrics.record_sync("bling", "ok", latency_ms=50.0)
    r = await client.get("/api/metrics")
    assert r.status_code == 200
    body = r.json()
    assert body["platforms"]["bling"]["ok"] == 1


@pytest.mark.asyncio
async def test_metrics_reset_endpoint(
    client: AsyncClient, make_user, auth_as
) -> None:
    from app.models import UserRole
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)

    await metrics.record_sync("bling", "ok", latency_ms=10.0)
    r = await client.post("/api/metrics/reset")
    assert r.status_code == 204
    assert await redis.hgetall("MET:syncs:bling") == {}
