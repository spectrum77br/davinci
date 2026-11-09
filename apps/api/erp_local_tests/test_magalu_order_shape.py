import asyncio
import copy
import json

import httpx
import pytest

from app.erp_local import magalu_order_shape as probe


def test_order_shape_exports_types_and_money_normalizer_but_no_identifiers_or_personal_values():
    raw = {
        "id": "PRIVATE-ORDER",
        "code": "PRIVATE-CODE",
        "created_at": "2026-11-05T12:00:00Z",
        "customer": {"name": "PRIVATE-BUYER", "document": "PRIVATE-DOCUMENT"},
        "items": [
            {
                "sku": "PRIVATE-SKU",
                "quantity": 2,
                "price": {"value": 12995, "normalizer": 100, "currency": "BRL"},
                "product": {"name": "PRIVATE-NAME"},
            }
        ],
    }
    projected = probe.shape(raw)
    assert "PRIVATE" not in json.dumps(projected) and "12995" not in json.dumps(projected)
    assert "2026-11-05" not in json.dumps(projected) and "customer" not in projected["fields"]
    assert projected["fields"]["created_at"] == {"type": "string", "format": "timestamp"}
    assert (
        projected["fields"]["items"]["first"]["fields"]["price"]["fields"]["normalizer"]["value"]
        == 100
    )
    probe.validate_shape(projected)
    bad = copy.deepcopy(projected)
    bad["fields"]["id"]["value"] = "PRIVATE"
    with pytest.raises(ValueError):
        probe.validate_shape(bad)


def test_magalu_schema_sample_is_one_fixed_get_without_refresh_or_following_next_link():
    calls = []

    async def check():
        def handler(request):
            calls.append(request)
            return httpx.Response(
                200,
                json={
                    "results": [{"id": "PRIVATE", "items": [{"sku": "PRIVATE", "quantity": 2}]}],
                    "meta": {"links": {"next": "https://evil.invalid/PRIVATE"}},
                },
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=False
        ) as client:
            result = await probe.read_account(client, {"access_token": "PRIVATE-TOKEN"})
        assert (
            result["state"] == "shape_observed"
            and result["sample_count"] == 1
            and result["has_more"]
        )
        assert "PRIVATE" not in json.dumps(result)

    asyncio.run(check())
    assert len(calls) == 1
    assert calls[0].method == "GET" and calls[0].url.path == "/seller/v1/orders"
    assert dict(calls[0].url.params) == {"_limit": "1", "_offset": "0"} and not calls[0].content


@pytest.mark.parametrize("status,state", [(401, "auth_denied"), (302, "provider_unavailable")])
def test_magalu_schema_failure_is_fixed_and_does_not_follow_redirect_or_refresh(status, state):
    calls = []

    async def check():
        def handler(request):
            calls.append(request)
            return httpx.Response(
                status, text="PRIVATE-ERROR", headers={"Location": "https://evil.invalid"}
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=False
        ) as client:
            result = await probe.read_account(client, {"access_token": "PRIVATE-TOKEN"})
        assert result["state"] == state and "PRIVATE" not in json.dumps(result)

    asyncio.run(check())
    assert len(calls) == 1
