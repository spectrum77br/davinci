"""Magalu dispatch confirmation without database or marketplace access."""

from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.services.magalu_shipment_status import (
    get_magalu_shipment_status,
    normalize_magalu_order_code,
    parse_magalu_shipment_status,
)

_CODE = "order-123-456"
_NOW = datetime(2026, 10, 1, 12, tzinfo=UTC)
_SHIPPED_AT = datetime(2026, 9, 30, 19, 27, 29, tzinfo=UTC)


@pytest.fixture
def payload():
    # Sanitized shape of Bling299836's Magalu response: no customer, payment,
    # address, seller, product or real identifiers are retained.
    return {
        "meta": {
            "page": {"limit": 10, "offset": 0, "count": 1, "max_limit": 50},
            "links": {"self": "?_offset=0&limit=10"},
        },
        "results": [{
            "id": "order-id-1", "code": _CODE, "status": "approved",
            "deliveries": [{
                "id": "delivery-id-1", "status": "shipped",
                "shipping": {
                    "shipped_at": "2026-09-30T19:27:29Z",
                    "handling_time": {"limit_date": "2026-09-29"},
                    "deadline": {"limit_date": "2026-10-16"},
                },
            }],
        }],
    }


def _parse(payload):
    return parse_magalu_shipment_status(payload, f"LU-{_CODE}", now=_NOW)


def _delivery(payload):
    return payload["results"][0]["deliveries"][0]


def test_real_shipment_shape_and_date_only_deadline(payload):
    result = _parse(payload)
    assert result.confirmed
    assert result.shipped_at == _SHIPPED_AT
    assert result.deadline is None
    assert result.reason is None


@pytest.mark.parametrize("prefix", ["", "LU-", "lu-", "Lu-"])
def test_normalizes_only_local_prefix(payload, prefix):
    assert normalize_magalu_order_code(prefix + _CODE) == _CODE
    assert parse_magalu_shipment_status(payload, prefix + _CODE, now=_NOW).confirmed


@pytest.mark.parametrize("code", [None, "", " ", "LU-", "lu- "])
def test_invalid_local_code(payload, code):
    assert not parse_magalu_shipment_status(payload, code, now=_NOW).confirmed


@pytest.mark.parametrize("code", [None, "", "LU-order-123-456", "order123456", "other"])
def test_response_code_must_match_exactly(payload, code):
    payload["results"][0]["code"] = code
    assert _parse(payload).reason == "code_mismatch"


@pytest.mark.parametrize("field", ["id", "code"])
def test_missing_order_identity(payload, field):
    del payload["results"][0][field]
    assert not _parse(payload).confirmed


@pytest.mark.parametrize("orders", [[], [None], [{}, {}]])
def test_ambiguous_or_malformed_results(payload, orders):
    payload["results"] = orders
    assert not _parse(payload).confirmed


def test_duplicate_order_match_is_not_selected(payload):
    payload["results"].append(deepcopy(payload["results"][0]))
    assert _parse(payload).reason == "ambiguous_order"


@pytest.mark.parametrize("meta", [None, {}, {"page": {}}, {"page": []}])
def test_missing_or_malformed_pagination(payload, meta):
    payload["meta"] = meta
    assert _parse(payload).reason == "pagination_ambiguous"


@pytest.mark.parametrize(
    ("field", "value"),
    [("offset", 10), ("offset", False), ("count", 2), ("count", 0),
     ("count", "1"), ("limit", 1), ("total", 2), ("total_count", True)],
)
def test_page_must_prove_only_one_result(payload, field, value):
    payload["meta"]["page"][field] = value
    assert _parse(payload).reason == "pagination_ambiguous"


@pytest.mark.parametrize("container", ["meta", "root"])
@pytest.mark.parametrize("direction", ["next", "previous", "prev"])
def test_pagination_link_is_ambiguous(payload, container, direction):
    target = payload["meta"] if container == "meta" else payload
    target.setdefault("links", {})[direction] = "?_offset=10"
    assert _parse(payload).reason == "pagination_ambiguous"


@pytest.mark.parametrize("status", ["cancelled", " CANCELED ", " frozen "])
def test_cancelled_order_cannot_be_confirmed_by_stale_delivery(payload, status):
    payload["results"][0]["status"] = status
    assert _parse(payload).reason == "order_not_shipped"


@pytest.mark.parametrize("deliveries", [None, [], {}, [None], [{}]])
def test_missing_or_malformed_deliveries(payload, deliveries):
    payload["results"][0]["deliveries"] = deliveries
    assert _parse(payload).reason == "invalid_deliveries"


@pytest.mark.parametrize("identity", [None, "", " ", 123])
def test_delivery_requires_identity(payload, identity):
    _delivery(payload)["id"] = identity
    assert _parse(payload).reason == "invalid_deliveries"


def test_duplicate_delivery_ids_rejected(payload):
    duplicate = deepcopy(_delivery(payload))
    duplicate["id"] = " DELIVERY-ID-1 "
    payload["results"][0]["deliveries"].append(duplicate)
    assert _parse(payload).reason == "invalid_deliveries"


@pytest.mark.parametrize("status", [None, "", "cancelled", "frozen", "invoiced", "new", "approved"])
def test_every_delivery_must_have_confirmed_dispatch(payload, status):
    other = deepcopy(_delivery(payload))
    other.update(id="delivery-id-2", status=status)
    payload["results"][0]["deliveries"].append(other)
    assert _parse(payload).reason == "not_shipped"


def test_complete_order_uses_latest_actual_dispatch_time(payload):
    _delivery(payload)["status"] = " SHIPPED "
    other = deepcopy(_delivery(payload))
    other.update(id="delivery-id-2", status=" Delivered ")
    other["shipping"]["shipped_at"] = "2026-09-30T17:30:00-03:00"
    payload["results"][0]["deliveries"].append(other)
    result = _parse(payload)
    assert result.confirmed
    assert result.shipped_at == datetime(2026, 9, 30, 20, 30, tzinfo=UTC)


@pytest.mark.parametrize("field", ["posting_date", "shipped_at"])
def test_documented_dispatch_fields(payload, field):
    delivery = _delivery(payload)
    timestamp = delivery["shipping"].pop("shipped_at")
    target = delivery["shipping"] if field == "posting_date" else delivery
    target[field] = timestamp
    result = _parse(payload)
    assert result.confirmed
    assert result.shipped_at == _SHIPPED_AT


def test_equivalent_timestamps_in_all_fields_are_unambiguous(payload):
    delivery = _delivery(payload)
    delivery["shipped_at"] = "2026-09-30T16:27:29-03:00"
    delivery["shipping"]["posting_date"] = "2026-09-30T19:27:29+00:00"
    result = _parse(payload)
    assert result.confirmed
    assert result.shipped_at == _SHIPPED_AT


@pytest.mark.parametrize("field", ["posting_date", "shipped_at"])
def test_conflicting_timestamp_fields_do_not_confirm(payload, field):
    delivery = _delivery(payload)
    target = delivery["shipping"] if field == "posting_date" else delivery
    target[field] = "2026-09-30T19:28:29Z"
    assert _parse(payload).reason == "conflicting_shipped_at"


@pytest.mark.parametrize("field", ["posting_date", "shipped_at"])
def test_invalid_documented_timestamp_cannot_hide_behind_compatibility_field(payload, field):
    delivery = _delivery(payload)
    target = delivery["shipping"] if field == "posting_date" else delivery
    target[field] = "2026-09-30"
    assert _parse(payload).reason == "invalid_shipped_at"


@pytest.mark.parametrize("missing", ["field", "null", "shipping"])
def test_status_can_confirm_without_inventing_missing_timestamp(payload, missing):
    delivery = _delivery(payload)
    if missing == "field":
        del delivery["shipping"]["shipped_at"]
    elif missing == "shipping":
        del delivery["shipping"]
    else:
        delivery["shipping"]["shipped_at"] = None
    result = _parse(payload)
    assert result.confirmed
    assert result.shipped_at is None


def test_partial_timestamps_cannot_date_complete_shipment(payload):
    other = deepcopy(_delivery(payload))
    other.update(id="delivery-id-2", shipping={})
    payload["results"][0]["deliveries"].append(other)
    result = _parse(payload)
    assert result.confirmed
    assert result.shipped_at is None


@pytest.mark.parametrize(
    "timestamp",
    ["", "2026-09-30", "2026-09-30T19:27:29", "2026-09-30T19:27:29-00:00",
     "2026-09-31T19:27:29Z", "2026-09-30T19:27:29+25:00", "2026-09-30T19:27:29+00:60",
     "9999-12-31T23:59:59-23:00", 1, True, {}],
)
def test_invalid_or_ambiguous_timestamp_does_not_confirm(payload, timestamp):
    _delivery(payload)["shipping"]["shipped_at"] = timestamp
    assert _parse(payload).reason == "invalid_shipped_at"


def test_future_shipped_timestamp_does_not_confirm(payload):
    _delivery(payload)["shipping"]["shipped_at"] = "2026-10-01T12:00:01Z"
    assert _parse(payload).reason == "future_shipped_at"


def test_naive_clock_cannot_validate_shipment(payload):
    result = parse_magalu_shipment_status(payload, _CODE, now=datetime(2026, 10, 1))
    assert result.reason == "invalid_now"


def test_dispatch_deadline_uses_timezone_and_earliest_delivery(payload):
    delivery = _delivery(payload)
    delivery["shipping"]["handling_time"]["limit_date"] = "2026-09-30T17:00:00-03:00"
    other = deepcopy(delivery)
    other.update(id="delivery-id-2", status="approved")
    other["shipping"]["handling_time"]["limit_date"] = "2026-09-30T15:00:00-03:00"
    payload["results"][0]["deliveries"].append(other)
    result = _parse(payload)
    assert not result.confirmed
    assert result.deadline == datetime(2026, 9, 30, 18, tzinfo=UTC)


@pytest.mark.asyncio
async def test_helper_gets_only_exact_code_with_bounded_page(payload):
    client = SimpleNamespace(_request=AsyncMock(return_value=httpx.Response(200, json=payload)))
    result = await get_magalu_shipment_status(client, f"lu-{_CODE}", now=_NOW)
    assert result.confirmed
    client._request.assert_awaited_once_with(
        "GET", "/seller/v1/orders", params={"code": _CODE, "_limit": 10, "_offset": 0}
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [201, 204, 400, 401, 403, 404, 429, 500])
async def test_http_error_never_confirms_or_exposes_body(status):
    client = SimpleNamespace(_request=AsyncMock(
        return_value=httpx.Response(status, text="sensitive response")
    ))
    result = await get_magalu_shipment_status(client, _CODE, now=_NOW)
    assert not result.confirmed
    assert result.reason == "http_error"
    assert "sensitive" not in repr(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [httpx.ConnectError("sensitive"), RuntimeError("sensitive")])
async def test_request_failure_is_conservative_and_private(error):
    client = SimpleNamespace(_request=AsyncMock(side_effect=error))
    result = await get_magalu_shipment_status(client, _CODE, now=_NOW)
    assert not result.confirmed
    assert result.reason == "request_failed"
    assert "sensitive" not in repr(result)


@pytest.mark.asyncio
async def test_invalid_json_does_not_confirm():
    client = SimpleNamespace(_request=AsyncMock(return_value=httpx.Response(200, text="oops")))
    result = await get_magalu_shipment_status(client, _CODE, now=_NOW)
    assert result.reason == "invalid_response"


@pytest.mark.asyncio
async def test_invalid_code_does_not_call_api():
    client = SimpleNamespace(_request=AsyncMock())
    result = await get_magalu_shipment_status(client, "LU-", now=_NOW)
    assert not result.confirmed
    client._request.assert_not_awaited()
