"""STO01/PRI01 contracts prevent stock and price from overwriting each other."""

import copy
from email import policy
from email.parser import BytesParser

import httpx
import pytest
import respx

from app.services.marketplaces.carrefour import (
    CARREFOUR_API_BASE,
    CarrefourClient,
    CarrefourError,
    build_price_update,
    build_stock_update,
)

IMPORT_ID = "7d98c3c0-81fe-407d-9721-d4b01951c8d8"


def client():
    return CarrefourClient({"api_key": "test-key", "shop_id": "64421"})


def offer():
    pricing = {
        "channel_code": None, "price": 19.90, "unit_origin_price": 19.90,
        "unit_discount_price": None,
        "volume_prices": [{
            "quantity_threshold": 1, "price": 19.90,
            "unit_origin_price": 19.90, "unit_discount_price": None,
        }],
    }
    return {
        "offer_id": 123, "shop_sku": "POOFY;001", "quantity": 3,
        "fulfillment": {"center": {"code": "DEFAULT"}}, "warehouses": [],
        "currency_iso_code": "BRL", "all_prices": [pricing],
        "applicable_pricing": copy.deepcopy(pricing),
    }


def status(**changes):
    return {
        "import_id": IMPORT_ID, "shop_id": 64421, "status": "COMPLETE",
        "lines_in_error": 0, "lines_in_success": 1, "has_error_report": False,
        **changes,
    }


def csv_file(request):
    header = f'Content-Type: {request.headers["content-type"]}\r\n\r\n'.encode()
    message = BytesParser(policy=policy.default).parsebytes(header + request.content)
    parts = list(message.iter_parts())
    assert len(parts) == 1
    assert parts[0].get_param("name", header="content-disposition") == "file"
    return parts[0].get_payload(decode=True).decode()


def test_stock_builder_ignores_promotions_and_unrelated_offer_fields():
    raw = offer()
    raw["all_prices"] = [{"some_future_pricing_format": {"discount": 2}}]
    before = copy.deepcopy(raw)
    assert build_stock_update(raw, qty=0, expected_sku="POOFY;001") == {
        "shop_sku": "POOFY;001", "quantity": 0,
    }
    assert raw == before


@pytest.mark.parametrize("field,value", [
    ("warehouses", [{"code": "A", "quantity": 1}]),
    ("fulfillment", {"center": {"code": "OPERATOR"}}),
    ("quantity", True),
])
def test_stock_rejects_unsupported_inventory_ownership(field, value):
    raw = offer()
    raw[field] = value
    with pytest.raises(CarrefourError):
        build_stock_update(raw, qty=2, expected_sku="POOFY;001")


def test_price_builder_works_without_stock_or_shipping_fields():
    raw = offer()
    for field in ("quantity", "fulfillment", "warehouses"):
        del raw[field]
    before = copy.deepcopy(raw)
    assert build_price_update(raw, price="22.50", expected_sku="POOFY;001") == {
        "shop_sku": "POOFY;001", "price": 22.50,
    }
    assert raw == before


@pytest.mark.parametrize("change", ["discount", "channel", "volume", "context", "dropship"])
def test_price_rejects_data_that_a_minimal_price_import_would_erase(change):
    raw = offer()
    price = raw["all_prices"][0]
    if change == "discount":
        raw["discount"] = {"discount_price": 15}
    elif change == "channel":
        price["channel_code"] = "SPECIAL"
    elif change == "volume":
        price["volume_prices"].append({"quantity_threshold": 10, "unit_origin_price": 10})
    elif change == "context":
        price["context"] = {"start_date": "2026-12-01T00:00:00Z"}
    else:
        raw["retail_prices"] = [{"unit_origin_price": 30}]
    with pytest.raises(CarrefourError):
        build_price_update(raw, price=22.50, expected_sku="POOFY;001")


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,items,expected", [
    ("stock", [{"shop_sku": "POOFY;001", "quantity": 0}], 'offer-sku;quantity\n"POOFY;001";0\n'),
    ("pricing", [{"shop_sku": "POOFY;001", "price": 22.5}], 'offer-sku;price\n"POOFY;001";22.50\n'),
])
async def test_partial_import_sends_only_its_own_field(kind, items, expected):
    with respx.mock() as router:
        route = router.post(f"{CARREFOUR_API_BASE}/offers/{kind}/imports").respond(
            201, json={"import_id": IMPORT_ID},
        )
        c = client()
        method = c.submit_stock_updates if kind == "stock" else c.submit_price_updates
        assert await method(items) == IMPORT_ID
        request = route.calls[0].request
        assert request.url.params["shop_id"] == "64421"
        assert request.headers["authorization"] == "test-key"
        assert csv_file(request) == expected
        assert len(router.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("http_status", [403, 404, 405, 500, 503])
async def test_endpoint_failure_never_falls_back_to_full_offer_or_retries(http_status):
    with respx.mock() as router:
        route = router.post(f"{CARREFOUR_API_BASE}/offers/stock/imports").respond(http_status)
        with pytest.raises(CarrefourError) as caught:
            await client().submit_stock_updates([{"shop_sku": "P1", "quantity": 0}])
        assert caught.value.http_status == http_status
        if http_status >= 500:
            assert caught.value.code == "submission_unknown"
        assert route.call_count == len(router.calls) == 1


@pytest.mark.asyncio
async def test_timeout_is_unknown_and_never_retried():
    with respx.mock() as router:
        route = router.post(f"{CARREFOUR_API_BASE}/offers/pricing/imports").mock(
            side_effect=httpx.ReadTimeout("test"),
        )
        with pytest.raises(CarrefourError, match="desconhecido"):
            await client().submit_price_updates([{"shop_sku": "P1", "price": 10}])
        assert route.call_count == 1


@pytest.mark.asyncio
async def test_missing_import_id_is_not_reported_as_success():
    with respx.mock() as router:
        router.post(f"{CARREFOUR_API_BASE}/offers/stock/imports").respond(201, json={})
        with pytest.raises(CarrefourError) as caught:
            await client().submit_stock_updates([{"shop_sku": "P1", "quantity": 1}])
        assert caught.value.code == "submission_unknown"


@pytest.mark.asyncio
async def test_cross_field_payload_is_rejected_before_http():
    with respx.mock() as router:
        with pytest.raises(CarrefourError):
            await client().submit_stock_updates([{"shop_sku": "P1", "quantity": 1, "price": 5}])
        assert len(router.calls) == 0


@pytest.mark.asyncio
async def test_stock_status_is_single_object_and_shop_scoped():
    with respx.mock() as router:
        route = router.get(
            f"{CARREFOUR_API_BASE}/offers/stock/imports/{IMPORT_ID}/status"
        ).respond(200, json=status())
        result = await client().get_stock_import(IMPORT_ID)
        assert result["status"] == "COMPLETE" and result["lines_in_pending"] == 0
        assert route.calls[0].request.url.params["shop_id"] == "64421"


@pytest.mark.asyncio
async def test_pricing_status_uses_filtered_list_and_offer_errors():
    with respx.mock() as router:
        route = router.get(f"{CARREFOUR_API_BASE}/offers/pricing/imports").respond(
            200, json={"data": [status(offers_in_error=1)]},
        )
        result = await client().get_price_import(IMPORT_ID)
        assert result["lines_in_error"] == 1
        query = route.calls[0].request.url.params
        assert query["shop_id"] == "64421" and query["import_id"] == IMPORT_ID


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"shop_id": 999}, {"import_id": "other"}, {"lines_in_error": True},
    {"lines_in_success": None}, {"status": "UNKNOWN"},
])
async def test_confirmation_rejects_wrong_identity_or_malformed_counts(changes):
    with respx.mock() as router:
        router.get(f"{CARREFOUR_API_BASE}/offers/pricing/imports").respond(
            200, json={"data": [status(**changes)]},
        )
        with pytest.raises(CarrefourError):
            await client().get_price_import(IMPORT_ID)


@pytest.mark.asyncio
async def test_completed_stock_import_without_optional_counts_returns_unknown_counts():
    data = status()
    del data["lines_in_error"]
    del data["lines_in_success"]
    with respx.mock() as router:
        router.get(f"{CARREFOUR_API_BASE}/offers/stock/imports/{IMPORT_ID}/status").respond(
            200, json=data,
        )
        result = await client().get_stock_import(IMPORT_ID)
        assert result["status"] == "COMPLETE"
        assert result["lines_in_error"] is None
        assert result["lines_in_success"] is None


@pytest.mark.asyncio
async def test_of22_reads_bound_offer_by_numeric_id_with_explicit_shop():
    with respx.mock() as router:
        route = router.get(f"{CARREFOUR_API_BASE}/offers/123").respond(200, json=offer())
        assert (await client().get_offer(123))["shop_sku"] == "POOFY;001"
        assert route.calls[0].request.url.params["shop_id"] == "64421"
        route.respond(200, json={**offer(), "offer_id": 999})
        with pytest.raises(CarrefourError) as caught:
            await client().get_offer(123)
        assert caught.value.code == "offer_id_mismatch"


@pytest.mark.asyncio
@pytest.mark.parametrize("ident", [0, -1, True, "../account", "https://evil.example/"])
async def test_of22_rejects_invalid_id_before_http(ident):
    with respx.mock() as router:
        with pytest.raises(CarrefourError):
            await client().get_offer(ident)
        assert len(router.calls) == 0
