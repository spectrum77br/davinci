"""Isolated HTTP-contract tests. Run pytest --noconftest; no database connections."""

from __future__ import annotations

import asyncio
import copy
from datetime import UTC, datetime
from email.utils import format_datetime
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from app.services.marketplaces import carrefour
from app.services.marketplaces.base import SyncStatus
from app.services.marketplaces.carrefour import (
    ACCOUNT_CACHE_KEY,
    CARREFOUR_API_BASE,
    CarrefourClient,
    CarrefourError,
    _normalize_offer,
)


@pytest.fixture(autouse=True)
def clear_cache(monkeypatch):
    monkeypatch.setattr(carrefour, "_ACCOUNT_CACHE", {})
    monkeypatch.setattr(carrefour, "_ACCOUNT_LOCK", asyncio.Lock())


def creds(**overrides):
    return {"api_key": "test-carrefour-api-key", "shop_id": "123", **overrides}


def account(**overrides):
    return {"shop_id": 123, "shop_name": "VR", "shop_state": "OPEN", **overrides}


def offer(offer_id=100, **overrides):
    return {
        "offer_id": offer_id, "shop_sku": "B001.20+KIT", "product_sku": "catalog-999",
        "product_title": "Produto", "quantity": 8, "price": 19.9, "active": True,
        "category_code": "c1", "category_label": "Categoria", **overrides,
    }


@pytest.mark.asyncio
@respx.mock
async def test_connection_auth_shop_identity_and_server_metadata():
    callback = AsyncMock()
    route = respx.get(f"{CARREFOUR_API_BASE}/account").respond(200, json=account())
    original = creds()
    client = CarrefourClient(original, callback)

    result = await client.test_connection()

    assert result.ok
    assert result.info["shop_id"] == 123
    assert result.info["shop_name"] == "VR"
    assert result.info["mode"] == "read_only"
    assert not result.info["cached"]
    request = route.calls[0].request
    assert request.headers["Authorization"] == original["api_key"]
    assert request.url.params["shop_id"] == "123"
    assert request.url.scheme == "https"
    assert ACCOUNT_CACHE_KEY not in original
    saved = callback.call_args.args[0]
    assert saved["api_key"] == original["api_key"]
    assert saved[ACCOUNT_CACHE_KEY]["ok"]
    assert original["api_key"] not in str(result)


@pytest.mark.asyncio
@respx.mock
async def test_check_persisted_reused_24_hours_and_key_or_shop_change_invalidates(monkeypatch):
    route = respx.get(f"{CARREFOUR_API_BASE}/account").respond(200, json=account())
    callback = AsyncMock()
    first = CarrefourClient(creds(), callback)
    assert (await first.test_connection()).ok
    saved = callback.call_args.args[0]
    carrefour._ACCOUNT_CACHE.clear()  # simulate another process, loading persisted metadata
    assert (await CarrefourClient(saved).test_connection()).info["cached"]
    assert route.call_count == 1
    changed_key = CarrefourClient({**saved, "api_key": "different-key"})
    assert (await changed_key.test_connection()).ok
    assert route.call_count == 2
    route.respond(200, json=account(shop_id=456, shop_name="Poofy"))
    assert (await CarrefourClient({**saved, "shop_id": 456}).test_connection()).ok
    assert route.call_count == 3
    route.respond(200, json=account())
    checked_at = saved[ACCOUNT_CACHE_KEY]["checked_at"]
    monkeypatch.setattr(carrefour.time, "time", lambda: checked_at + 86400)
    assert (await CarrefourClient(saved).test_connection()).ok
    assert route.call_count == 4


@pytest.mark.asyncio
@respx.mock
async def test_concurrent_clients_share_one_account_call():
    route = respx.get(f"{CARREFOUR_API_BASE}/account").respond(200, json=account())
    results = await asyncio.gather(*[CarrefourClient(creds()).test_connection() for _ in range(5)])
    assert all(result.ok for result in results)
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_wrong_shop_prevents_listing_read():
    respx.get(f"{CARREFOUR_API_BASE}/account").respond(200, json=account(shop_id=999))
    offers = respx.get(f"{CARREFOUR_API_BASE}/offers")
    with pytest.raises(CarrefourError, match="outra loja"):
        _ = [item async for item in CarrefourClient(creds()).list_listings()]
    assert not offers.called


@pytest.mark.parametrize("state", ["CLOSE", "SUSPENDED", "TERMINATED", "UNKNOWN", {}, None])
@pytest.mark.asyncio
@respx.mock
async def test_closed_or_unknown_store_is_not_success(state):
    respx.get(f"{CARREFOUR_API_BASE}/account").respond(200, json=account(shop_state=state))
    assert not (await CarrefourClient(creds()).test_connection()).ok


@pytest.mark.parametrize("override", [
    {"api_key": ""}, {"api_key": "bad\r\nheader"}, {"api_key": "á"},
    {"shop_id": None}, {"shop_id": 0}, {"shop_id": True}, {"shop_id": "abc"},
    {"shop_id": 1.5}, {"shop_id": "9" * 5000},
])
@pytest.mark.asyncio
@respx.mock
async def test_invalid_credentials_do_no_network(override):
    assert not (await CarrefourClient(creds(**override)).test_connection()).ok
    assert not respx.calls


@pytest.mark.parametrize("status", [301, 302, 307, 401, 403, 500])
@pytest.mark.asyncio
@respx.mock
async def test_status_errors_are_safe_and_redirect_is_never_followed(status):
    route = respx.get(f"{CARREFOUR_API_BASE}/account").respond(
        status, text="test-carrefour-api-key secret-response",
        headers={"Location": "https://untrusted.example/steal"},
    )
    result = await CarrefourClient(creds()).test_connection()
    assert not result.ok
    assert "secret-response" not in str(result)
    assert "test-carrefour-api-key" not in str(result)
    assert len(respx.calls) == route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_timeout_and_invalid_json_are_safe():
    route = respx.get(f"{CARREFOUR_API_BASE}/account").mock(
        side_effect=httpx.ReadTimeout("test-carrefour-api-key secret-exception")
    )
    result = await CarrefourClient(creds()).test_connection()
    assert not result.ok and "test-carrefour-api-key" not in str(result)
    route.respond(200, text="not-json secret-response")
    result = await CarrefourClient(creds(api_key="other-key")).test_connection()
    assert not result.ok and "secret-response" not in str(result)


@pytest.mark.asyncio
@respx.mock
async def test_account_does_not_retry_429_and_caches_failure():
    route = respx.get(f"{CARREFOUR_API_BASE}/account").respond(
        429, headers={"Retry-After": "120"},
    )
    client = CarrefourClient(creds())
    result = await client.test_connection()
    assert not result.ok and result.info["retry_after"] == 120
    assert not (await client.test_connection()).ok
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_cache_save_failure_retries_persistence_not_account_query():
    route = respx.get(f"{CARREFOUR_API_BASE}/account").respond(200, json=account())
    callback = AsyncMock(side_effect=RuntimeError("api_key secret"))
    client = CarrefourClient(creds(), callback)
    result = await client.test_connection()
    assert not result.ok and "secret" not in str(result)
    callback.side_effect = None
    assert (await client.test_connection()).ok
    assert callback.await_count == 2
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_paginated_offers_scope_and_normalization():
    respx.get(f"{CARREFOUR_API_BASE}/account").respond(200, json=account())
    route = respx.get(f"{CARREFOUR_API_BASE}/offers").mock(side_effect=[
        httpx.Response(200, json={"offers": [offer(1), offer(2)], "total_count": 3}),
        httpx.Response(200, json={"offers": [offer(3, active=False)], "total_count": 3}),
    ])
    rows = [row async for row in CarrefourClient(creds()).list_listings(page_size=2)]
    assert [row["external_id"] for row in rows] == ["1", "2", "3"]
    assert rows[0]["sku"] == "B001.20+KIT"
    assert rows[0]["stock"] == 8 and rows[0]["price"] == 1990
    assert rows[0]["category"] == "Categoria" and rows[0]["status"] == "active"
    assert rows[2]["status"] == "inactive"
    assert rows[0]["thumbnail_url"] is None
    assert [call.request.url.params["offset"] for call in route.calls] == ["0", "2"]
    assert all(call.request.url.params["shop_id"] == "123" for call in route.calls)


@pytest.mark.asyncio
@respx.mock
async def test_short_pages_use_total_count_and_max_pages_caps_work():
    respx.get(f"{CARREFOUR_API_BASE}/account").respond(200, json=account())
    route = respx.get(f"{CARREFOUR_API_BASE}/offers").mock(side_effect=[
        httpx.Response(200, json={"offers": [offer(1)], "total_count": 8}),
        httpx.Response(200, json={"offers": [offer(2)], "total_count": 8}),
    ])
    rows = [row async for row in CarrefourClient(creds()).list_listings(page_size=999, max_pages=2)]
    assert len(rows) == 2
    assert route.calls[0].request.url.params["max"] == "100"
    assert route.calls[1].request.url.params["offset"] == "1"


@pytest.mark.asyncio
@respx.mock
async def test_repeated_page_at_total_count_cannot_silently_finish():
    respx.get(f"{CARREFOUR_API_BASE}/account").respond(200, json=account())
    route = respx.get(f"{CARREFOUR_API_BASE}/offers").respond(
        200, json={"offers": [offer(1)], "total_count": 2},
    )
    with pytest.raises(CarrefourError, match="não avançou"):
        _ = [row async for row in CarrefourClient(creds()).list_listings(page_size=1)]
    assert route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_empty_page_before_reported_total_is_incomplete():
    respx.get(f"{CARREFOUR_API_BASE}/account").respond(200, json=account())
    respx.get(f"{CARREFOUR_API_BASE}/offers").mock(side_effect=[
        httpx.Response(200, json={"offers": [offer(1), offer(2)], "total_count": 3}),
        httpx.Response(200, json={"offers": [], "total_count": 3}),
    ])
    with pytest.raises(CarrefourError) as raised:
        _ = [row async for row in CarrefourClient(creds()).list_listings(page_size=2)]
    assert raised.value.code == "incomplete_results"


@pytest.mark.asyncio
@respx.mock
async def test_overlapping_pages_cannot_reach_total_with_missing_unique_records():
    respx.get(f"{CARREFOUR_API_BASE}/account").respond(200, json=account())
    respx.get(f"{CARREFOUR_API_BASE}/offers").mock(side_effect=[
        httpx.Response(200, json={"offers": [offer(1), offer(2)], "total_count": 4}),
        httpx.Response(200, json={"offers": [offer(2), offer(3)], "total_count": 4}),
    ])
    with pytest.raises(CarrefourError) as raised:
        _ = [row async for row in CarrefourClient(creds()).list_listings(page_size=2)]
    assert raised.value.code == "incomplete_results"


@pytest.mark.parametrize("payload", [{}, {"offers": {}}, {"offers": [None]}])
@pytest.mark.asyncio
@respx.mock
async def test_malformed_pages_are_errors_not_empty_success(payload):
    respx.get(f"{CARREFOUR_API_BASE}/account").respond(200, json=account())
    respx.get(f"{CARREFOUR_API_BASE}/offers").respond(200, json=payload)
    with pytest.raises(CarrefourError, match="página inválida"):
        _ = [row async for row in CarrefourClient(creds()).list_listings()]


@pytest.mark.asyncio
@respx.mock
async def test_read_retry_obeys_short_retry_after(monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr(carrefour.asyncio, "sleep", sleep)
    route = respx.get(f"{CARREFOUR_API_BASE}/offers").mock(side_effect=[
        httpx.Response(429, headers={"Retry-After": "3"}),
        httpx.Response(200, json={"offers": []}),
    ])
    assert await CarrefourClient(creds())._read_json("/offers") == {"offers": []}
    sleep.assert_awaited_once_with(3)
    assert route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_long_retry_after_surfaces_without_early_retry(monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr(carrefour.asyncio, "sleep", sleep)
    monkeypatch.setattr(carrefour.time, "time", lambda: 1700000000)
    date = format_datetime(datetime.fromtimestamp(1700000120, tz=UTC), usegmt=True)
    route = respx.get(f"{CARREFOUR_API_BASE}/offers").respond(429, headers={"Retry-After": date})
    with pytest.raises(CarrefourError) as raised:
        await CarrefourClient(creds())._read_json("/offers")
    assert raised.value.retry_after == 120
    assert route.call_count == 1
    sleep.assert_not_awaited()


def test_offer_price_is_integer_cents_and_missing_values_are_not_zero():
    assert _normalize_offer(offer(price=123.45))["price"] == 12345
    assert _normalize_offer(offer(price="19.995"))["price"] == 2000
    normalized = _normalize_offer(offer(
        quantity=0, applicable_pricing={"price": 0}, price=49,
    ))
    assert normalized["price"] == 0 and normalized["stock"] == 0
    malformed = _normalize_offer(offer(price="NaN", quantity=-1, active="false"))
    assert malformed["price"] is None and malformed["stock"] is None
    assert malformed["status"] == "inactive"
    with pytest.raises(CarrefourError, match="sem ID"):
        _normalize_offer(offer(offer_id=None))


@pytest.mark.asyncio
@respx.mock
async def test_orders_read_has_shop_and_utc_filter():
    respx.get(f"{CARREFOUR_API_BASE}/account").respond(200, json=account())
    order = {"order_id": "123-A", "order_state": "SHIPPING"}
    route = respx.get(f"{CARREFOUR_API_BASE}/orders").respond(
        200, json={"orders": [order], "total_count": 1},
    )
    rows = [row async for row in CarrefourClient(creds()).list_orders(
        start_update_date="2026-10-26T09:00:00-03:00",
    )]
    assert rows == [order]
    assert route.calls[0].request.url.params["start_update_date"] == "2026-10-26T12:00:00+00:00"
    assert route.calls[0].request.url.params["shop_id"] == "123"


@pytest.mark.asyncio
@respx.mock
async def test_invalid_order_date_and_unapproved_path_do_no_network():
    client = CarrefourClient(creds())
    with pytest.raises(CarrefourError, match="fuso"):
        _ = [row async for row in client.list_orders(start_update_date="2026-10-26")]
    with pytest.raises(CarrefourError, match="não habilitada"):
        await client._read_json("https://other.example")
    assert not respx.calls


@pytest.mark.parametrize("force", [True, False])
@pytest.mark.asyncio
@respx.mock
async def test_stock_writes_blocked_even_when_forced(force):
    client = CarrefourClient(creds())
    before = copy.deepcopy(client.creds)
    result = await client.update_stock(None, 10, force=force)
    assert result.status is SyncStatus.REQUIRES_REVIEW
    assert result.error_code == "carrefour_write_not_enabled"
    assert result.qty_after is None
    assert client.creds == before
    assert not respx.calls
