import asyncio
import copy
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.erp_local import native_orders
from app.erp_local import tiktok_order_probe as probe
from app.erp_local.core import ERPError, dumps

ACCOUNT = "de471c7c-f270-48ad-9ed1-878ce78e7453"
CREDS = {
    "access_token": "PRIVATE-TOKEN",
    "app_key": "PRIVATE-KEY",
    "app_secret": "PRIVATE-SECRET",
    "shop_cipher": "PRIVATE-CIPHER",
    "shop_id": "123",
}


def raw_order(oid="7000000000000000001"):
    now = int((datetime.now(UTC) - timedelta(hours=1)).timestamp())
    return {
        "id": oid,
        "create_time": now - 3600,
        "update_time": now,
        "status": "AWAITING_SHIPMENT",
        "fulfillment_type": "FULFILLMENT_BY_SELLER",
        "payment": {"currency": "BRL", "total_amount": "30.01", "payment_method": "PRIVATE"},
        "recipient_address": {"full_address": "PRIVATE-ADDRESS"},
        "buyer_email": "PRIVATE-BUYER",
        "line_items": [
            {
                "id": "7000000000000000002",
                "product_id": "7000000000000000003",
                "sku_id": "7000000000000000004",
                "seller_sku": "demo.kit",
                "sale_price": "15.005",
                "currency": "BRL",
                "display_status": "AWAITING_SHIPMENT",
                "product_name": "PRIVATE-NAME",
                "sku_image": "PRIVATE-IMAGE",
            }
        ],
    }


def report():
    now = datetime.now(UTC)
    start, end = int(now.timestamp()) - 86400, int(now.timestamp()) - 3
    return {
        "version": 1,
        "source": "tiktok_orders_202309_read",
        "read_only": "on",
        "no_token_refresh": True,
        "external_business_writes": False,
        "limit_per_account": probe.LIMIT,
        "captured_at": now.isoformat(),
        "accounts": [
            {
                "integration_id": ACCOUNT,
                "platform": "tiktok",
                "credentials_revision": (now - timedelta(hours=2)).isoformat(),
                "checked_at": now.isoformat(),
                "state": "read_ok",
                "http_status": 200,
                "has_more": False,
                "pages_read": 1,
                "total_eligible": 1,
                "coverage_review": False,
                "identity_match": True,
                "shop_id": "123",
                "failure_stage": None,
                "date_from": datetime.fromtimestamp(start, UTC).isoformat(),
                "date_to": datetime.fromtimestamp(end, UTC).isoformat(),
                "orders": [probe.project(raw_order(), "123", start, end)],
            }
        ],
    }


def known(erp):
    with erp.connection() as db:
        db.execute(
            "INSERT INTO store_policies VALUES ('tiktok',?,'shadow',?,?)",
            (ACCOUNT, dumps({"name": "TikTok local"}), erp.now()),
        )


def test_tiktok_projection_preserves_missing_quantity_and_sale_price_without_guessing_or_pii():
    now = int(datetime.now(UTC).timestamp())
    raw = raw_order()
    got = probe.project(raw, "123", now - 86400, now - 3)
    assert "PRIVATE" not in json.dumps(got)
    item = got["items"][0]
    assert item["quantity"] is None and item["quantity_review"]
    assert item["sale_price"] == {"amount": "15.005", "currency": "BRL"}
    raw["line_items"][0].update(quantity=2, combined_listing_skus=[{"seller_sku": "PRIVATE"}])
    item = probe.project(raw, "123", now - 86400, now - 3)["items"][0]
    assert item["quantity"] == 2 and not item["quantity_review"] and item["composition_review"]


def test_tiktok_request_signature_binds_body_and_only_two_fixed_reads_are_possible():
    body = {"update_time_ge": 1700000000, "update_time_lt": 1700001000}
    plan = probe.request_plan(CREDS, "POST", probe.SEARCH, {}, body, now=1700002000)
    query = {k: v for k, v in plan["params"].items() if k != "sign"}
    material = (
        probe.SEARCH + "".join(k + query[k] for k in sorted(query)) + plan["content"].decode()
    )
    expected = hmac.new(
        b"PRIVATE-SECRET", ("PRIVATE-SECRET" + material + "PRIVATE-SECRET").encode(), hashlib.sha256
    ).hexdigest()
    assert plan["params"]["sign"] == expected
    assert "access_token" not in plan["params"]
    for method, path, query, payload in [
        ("POST", "/order/202309/orders/cancel", {}, body),
        ("GET", probe.SEARCH, {}, body),
        ("POST", probe.SEARCH, {"warehouse_id": "1"}, body),
        ("POST", probe.SEARCH, {}, {**body, "order_status": "CANCELLED"}),
    ]:
        with pytest.raises(probe.ReadError):
            probe.request_plan(CREDS, method, path, query, payload, now=1)


def test_tiktok_verified_shop_and_paginated_search_keep_same_window_and_cursors_private():
    calls = []

    async def check():
        def handler(request):
            calls.append(request)
            if request.url.path == probe.AUTH:
                data = {
                    "shops": [
                        {"id": "999", "cipher": "OTHER"},
                        {"id": "123", "cipher": "PRIVATE-CIPHER", "name": "PRIVATE"},
                    ]
                }
            else:
                second = request.url.params.get("page_token") == "PRIVATE-CURSOR"
                data = {
                    "orders": [
                        raw_order("7000000000000000005" if second else "7000000000000000001")
                    ],
                    "total_count": 2,
                    "next_page_token": "" if second else "PRIVATE-CURSOR",
                }
            return httpx.Response(200, json={"code": 0, "data": data})

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=False
        ) as client:
            result = await probe.read_account(client, CREDS, now=datetime.now(UTC))
        assert result["identity_match"] is True and result["shop_id"] == "123"
        assert result["pages_read"] == 2 and not result["has_more"] and len(result["orders"]) == 2
        assert result["state"] == "read_ok" and "PRIVATE" not in json.dumps(result)

    asyncio.run(check())
    assert [(r.method, r.url.path) for r in calls] == [
        ("GET", probe.AUTH),
        ("POST", probe.SEARCH),
        ("POST", probe.SEARCH),
    ]
    assert calls[1].content == calls[2].content
    assert set(json.loads(calls[1].content)) == {"update_time_ge", "update_time_lt"}


def test_shop_identity_mismatch_stops_before_any_order_query():
    calls = []

    async def check():
        def handler(request):
            calls.append(request)
            return httpx.Response(
                200, json={"code": 0, "data": {"shops": [{"id": "999", "cipher": "OTHER"}]}}
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(probe.ReadError, match="identity_mismatch"):
                await probe.read_account(client, CREDS, now=datetime.now(UTC))

    asyncio.run(check())
    assert len(calls) == 1


@pytest.mark.parametrize(
    "http,state", [(401, "auth_denied"), (429, "rate_limited"), (302, "provider_unavailable")]
)
def test_tiktok_provider_errors_do_not_retry_redirect_or_reveal_request_headers(http, state):
    calls = []

    async def check():
        def handler(request):
            calls.append(request)
            return httpx.Response(
                http, text="PRIVATE-ERROR", headers={"Location": "https://evil.invalid"}
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=False
        ) as client:
            with pytest.raises(probe.ReadError, match=state) as exc:
                await probe.read_account(client, CREDS, now=datetime.now(UTC))
            assert "PRIVATE" not in str(exc.value)

    asyncio.run(check())
    assert len(calls) == 1


def test_inconsistent_result_count_is_preserved_as_partial_read():
    async def check():
        def handler(request):
            data = (
                {"shops": [{"id": "123", "cipher": "PRIVATE-CIPHER"}]}
                if request.url.path == probe.AUTH
                else {"orders": [], "total_count": 2}
            )
            return httpx.Response(200, json={"code": 0, "data": data})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await probe.read_account(client, CREDS, now=datetime.now(UTC))

    result = asyncio.run(check())
    assert (
        result["state"] == "partial_read" and result["coverage_review"] and not result["has_more"]
    )


@pytest.mark.parametrize("count", [0, 1, False])
def test_omitted_order_array_is_empty_only_with_explicit_integer_zero_and_no_cursor(count):
    async def check():
        def handler(request):
            data = (
                {"shops": [{"id": "123", "cipher": "PRIVATE-CIPHER"}]}
                if request.url.path == probe.AUTH
                else {"total_count": count}
            )
            return httpx.Response(200, json={"code": 0, "data": data})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            if type(count) is int and count == 0:
                result = await probe.read_account(client, CREDS, now=datetime.now(UTC))
                assert result["state"] == "read_ok" and result["orders"] == []
                assert result["identity_match"] and result["total_eligible"] == 0
            else:
                with pytest.raises(probe.ReadError, match="invalid_response"):
                    await probe.read_account(client, CREDS, now=datetime.now(UTC))

    asyncio.run(check())


def test_tiktok_native_import_maps_exact_product_and_sku_ids_without_reserving_own_stock(erp):
    known(erp)
    read = report()
    before = erp.snapshot()
    assert native_orders.import_read(erp, read)["observations_added"] == 1
    assert native_orders.import_read(erp, read)["replayed"]
    with erp.connection() as db:
        db.execute(
            "INSERT INTO stock_routes VALUES ('tiktok-exact','demo.kit','tiktok',?,'TikTok local',"
            "'7000000000000000003','7000000000000000004','geral',1,'test')",
            (ACCOUNT,),
        )
        assert native_orders.snapshot(db)["orders"][0]["local_items"][0]["sku"] == "demo.kit"
        db.execute("UPDATE stock_routes SET variation_id='7000000000000000099'")
        assert native_orders.snapshot(db)["orders"][0]["local_items"][0]["state"] == "missing"
    after = erp.snapshot()
    for key in ["orders", "accounts", "movements", "products"]:
        assert before[key] == after[key]


@pytest.mark.parametrize(
    "field", ["recipient_address", "buyer_email", "shop_cipher", "access_token"]
)
def test_tiktok_projection_rejects_extra_fields_before_local_writes(erp, field):
    known(erp)
    read = report()
    read["accounts"][0]["orders"][0][field] = "PRIVATE"
    before = erp.snapshot()
    with pytest.raises(ERPError):
        native_orders.import_read(erp, read)
    assert erp.snapshot() == before


def test_quantity_flags_and_counts_cannot_be_changed_to_hide_review(erp):
    known(erp)
    for mutate in ["quantity", "count", "identity"]:
        read = copy.deepcopy(report())
        if mutate == "quantity":
            read["accounts"][0]["orders"][0]["items"][0]["quantity_review"] = False
        elif mutate == "count":
            read["accounts"][0]["total_eligible"] = 2
        else:
            read["accounts"][0]["identity_match"] = False
        with pytest.raises(ERPError):
            native_orders.import_read(erp, read)
