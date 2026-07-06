"""TikTok discount-price tests.

Parity with Shopee: TikTok's `update_discount_price` must never write the base
listing price. It locates the ONGOING Fixed-Price promotion activity that
carries the product/SKU and updates its `activity_price_amount`
(POST /promotion/202309/activities/search -> GET .../activities/{id} ->
PUT .../activities/{id}/products). No active fixed-price promotion is a FATAL
telling the user to create one; a flash-deal is SKIPPED; the base price is
never touched.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
import pytest
import respx

from app.services.marketplaces.base import SyncStatus
from app.services.marketplaces.tiktok import TIKTOK_BASE_URL, TikTokClient


def _creds() -> dict[str, Any]:
    return {
        "app_key": "ak",
        "app_secret": "as",
        "access_token": "tok",
        "refresh_token": "ref",
        "shop_cipher": "cipher",
        # far-future expiry so _ensure_fresh_token never fires a refresh call
        "token_expires_at": int(time.time()) + 3600,
    }


def _search_body(activities: list[dict]) -> dict:
    return {"code": 0, "message": "Success", "data": {"activities": activities, "next_page_token": ""}}


def _activity_body(activity: dict) -> dict:
    return {"code": 0, "message": "Success", "data": activity}


PROD = "P100"
SKU = "S200"


# ---------------------------------------------------------------- happy path (VARIATION)


@pytest.mark.asyncio
async def test_update_variation_activity_price_ok() -> None:
    client = TikTokClient(_creds())
    with respx.mock(base_url=TIKTOK_BASE_URL) as router:
        router.post("/promotion/202309/activities/search").mock(
            return_value=httpx.Response(
                200,
                json=_search_body(
                    [{"id": "A1", "activity_type": "FIXED_PRICE", "status": "ONGOING"}]
                ),
            )
        )
        router.get("/promotion/202309/activities/A1").mock(
            return_value=httpx.Response(
                200,
                json=_activity_body(
                    {
                        "activity_id": "A1",
                        "product_level": "VARIATION",
                        "products": [
                            {"id": PROD, "skus": [{"id": SKU, "activity_price": {"amount": "999.00", "currency": "BRL"}}]}
                        ],
                    }
                ),
            )
        )
        put_route = router.put("/promotion/202309/activities/A1/products").mock(
            return_value=httpx.Response(200, json={"code": 0, "message": "Success", "data": {"activity_id": "A1"}})
        )
        result = await client.update_discount_price(PROD, SKU, 299.0)

    assert result.status == SyncStatus.OK
    assert result.payload["via"] == "update_activity_product"
    assert result.payload["activity_id"] == "A1"
    assert put_route.called
    body = put_route.calls.last.request.content
    assert b'"activity_price_amount":"299.00"' in body
    assert b'"id":"S200"' in body
    # VARIATION level forces product- and sku-level quantities to -1
    assert b'"quantity_limit":-1' in body


# ---------------------------------------------------------------- happy path (PRODUCT)


@pytest.mark.asyncio
async def test_update_product_level_activity_price_ok() -> None:
    client = TikTokClient(_creds())
    with respx.mock(base_url=TIKTOK_BASE_URL) as router:
        router.post("/promotion/202309/activities/search").mock(
            return_value=httpx.Response(
                200,
                json=_search_body([{"id": "A2", "activity_type": "FIXED_PRICE"}]),
            )
        )
        router.get("/promotion/202309/activities/A2").mock(
            return_value=httpx.Response(
                200,
                json=_activity_body(
                    {
                        "activity_id": "A2",
                        "product_level": "PRODUCT",
                        "products": [
                            {"id": PROD, "activity_price": {"amount": "999.00"}, "quantity_limit": 50, "quantity_per_user": 5}
                        ],
                    }
                ),
            )
        )
        put_route = router.put("/promotion/202309/activities/A2/products").mock(
            return_value=httpx.Response(200, json={"code": 0, "data": {}})
        )
        result = await client.update_discount_price(PROD, "", 150.0)

    assert result.status == SyncStatus.OK
    body = put_route.calls.last.request.content
    assert b'"activity_price_amount":"150.00"' in body
    # PRODUCT level: skus must be [] and existing quantity limits preserved
    assert b'"skus":[]' in body
    assert b'"quantity_limit":50' in body
    assert b'"quantity_per_user":5' in body


# ---------------------------------------------------------------- add fallback


@pytest.mark.asyncio
async def test_add_to_single_fixed_price_activity() -> None:
    """Product not enrolled, but exactly one ongoing FIXED_PRICE activity
    exists -> add it (parity with Shopee add_discount_item)."""
    client = TikTokClient(_creds())
    with respx.mock(base_url=TIKTOK_BASE_URL) as router:
        router.post("/promotion/202309/activities/search").mock(
            return_value=httpx.Response(
                200, json=_search_body([{"id": "A3", "activity_type": "FIXED_PRICE"}])
            )
        )
        router.get("/promotion/202309/activities/A3").mock(
            return_value=httpx.Response(
                200,
                json=_activity_body(
                    {"activity_id": "A3", "product_level": "VARIATION", "products": []}
                ),
            )
        )
        put_route = router.put("/promotion/202309/activities/A3/products").mock(
            return_value=httpx.Response(200, json={"code": 0, "data": {}})
        )
        result = await client.update_discount_price(PROD, SKU, 88.0)

    assert result.status == SyncStatus.OK
    assert result.payload["via"] == "add_activity_product"
    assert put_route.called


# ---------------------------------------------------------------- no active promotion


@pytest.mark.asyncio
async def test_no_active_fixed_price_is_fatal() -> None:
    client = TikTokClient(_creds())
    with respx.mock(base_url=TIKTOK_BASE_URL, assert_all_called=False) as router:
        router.post("/promotion/202309/activities/search").mock(
            return_value=httpx.Response(200, json=_search_body([]))
        )
        result = await client.update_discount_price(PROD, SKU, 100.0)

    assert result.status == SyncStatus.FATAL
    assert result.error_code == "tiktok_no_active_activity"


# ---------------------------------------------------------------- flash sale -> skipped


@pytest.mark.asyncio
async def test_flashsale_is_skipped() -> None:
    client = TikTokClient(_creds())
    with respx.mock(base_url=TIKTOK_BASE_URL, assert_all_called=False) as router:
        router.post("/promotion/202309/activities/search").mock(
            return_value=httpx.Response(
                200, json=_search_body([{"id": "F1", "activity_type": "FLASHSALE"}])
            )
        )
        router.get("/promotion/202309/activities/F1").mock(
            return_value=httpx.Response(
                200,
                json=_activity_body(
                    {
                        "activity_id": "F1",
                        "product_level": "VARIATION",
                        "products": [{"id": PROD, "skus": [{"id": SKU}]}],
                    }
                ),
            )
        )
        result = await client.update_discount_price(PROD, SKU, 100.0)

    assert result.status == SyncStatus.SKIPPED
    assert result.error_code == "tiktok_flashsale_active"


# ---------------------------------------------------------------- ambiguous


@pytest.mark.asyncio
async def test_multiple_fixed_price_not_enrolled_is_ambiguous() -> None:
    client = TikTokClient(_creds())
    with respx.mock(base_url=TIKTOK_BASE_URL, assert_all_called=False) as router:
        router.post("/promotion/202309/activities/search").mock(
            return_value=httpx.Response(
                200,
                json=_search_body(
                    [
                        {"id": "A4", "activity_type": "FIXED_PRICE"},
                        {"id": "A5", "activity_type": "FIXED_PRICE"},
                    ]
                ),
            )
        )
        router.get("/promotion/202309/activities/A4").mock(
            return_value=httpx.Response(
                200, json=_activity_body({"activity_id": "A4", "product_level": "VARIATION", "products": []})
            )
        )
        router.get("/promotion/202309/activities/A5").mock(
            return_value=httpx.Response(
                200, json=_activity_body({"activity_id": "A5", "product_level": "VARIATION", "products": []})
            )
        )
        result = await client.update_discount_price(PROD, SKU, 100.0)

    assert result.status == SyncStatus.FATAL
    assert result.error_code == "tiktok_multiple_activities"


# ---------------------------------------------------------------- invalid price


@pytest.mark.asyncio
async def test_invalid_price_is_skipped_no_http() -> None:
    client = TikTokClient(_creds())
    with respx.mock(base_url=TIKTOK_BASE_URL, assert_all_called=False) as router:
        result = await client.update_discount_price(PROD, SKU, 0.0)
        assert len(router.calls) == 0
    assert result.status == SyncStatus.SKIPPED
    assert result.error_code == "invalid_price"
