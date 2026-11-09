import asyncio
import copy
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from app.erp_local import native_orders
from app.erp_local import shopee_order_probe as probe
from app.erp_local.core import ERPError, dumps

ACCOUNT = "de471c7c-f270-48ad-9ed1-878ce78e7453"
CREDS = {
    "shop_id": "123",
    "access_token": "PRIVATE-TOKEN",
    "partner_id": "456",
    "partner_key": "PRIVATE-KEY",
}


def raw_order(oid="261105ABC123", *, model=456, quantity=2):
    updated = int((datetime.now(UTC) - timedelta(hours=1)).timestamp())
    return {
        "order_sn": oid,
        "create_time": updated - 3600,
        "update_time": updated,
        "currency": "BRL",
        "order_status": "READY_TO_SHIP",
        "total_amount": Decimal("20.25"),
        "fulfillment_flag": "fulfilled_by_local_seller",
        "buyer_user_id": "PRIVATE-BUYER",
        "recipient_address": {"name": "PRIVATE-NAME", "address": "PRIVATE-ADDRESS"},
        "item_list": [
            {
                "item_id": 9876543210123456789,
                "model_id": model,
                "item_sku": "PARENT-SKU",
                "model_sku": "demo.kit" if model else "",
                "model_quantity_purchased": quantity,
                "model_discounted_price": Decimal("10.125"),
                "item_name": "PRIVATE-TITLE",
                "image_info": {"image_url": "PRIVATE-URL"},
            }
        ],
    }


def report():
    now = datetime.now(UTC)
    start, end = int(now.timestamp()) - 86400, int(now.timestamp()) - 3
    return {
        "version": 1,
        "source": "shopee_orders_v2_read",
        "read_only": "on",
        "no_token_refresh": True,
        "external_business_writes": False,
        "captured_at": now.isoformat(),
        "limit_per_account": 100,
        "accounts": [
            {
                "integration_id": ACCOUNT,
                "platform": "shopee",
                "credentials_revision": (now - timedelta(hours=2)).isoformat(),
                "checked_at": now.isoformat(),
                "state": "read_ok",
                "http_status": 200,
                "has_more": False,
                "missing_details": 0,
                "date_from": datetime.fromtimestamp(start, UTC).isoformat(),
                "date_to": datetime.fromtimestamp(end, UTC).isoformat(),
                "failure_stage": None,
                "orders": [probe.project(raw_order(), "123", start, end)],
            }
        ],
    }


def known(erp):
    with erp.connection() as db:
        db.execute(
            "INSERT INTO store_policies VALUES ('shopee',?,'shadow',?,?)",
            (ACCOUNT, dumps({"name": "Shopee de demonstração"}), erp.now()),
        )


def test_shopee_projection_keeps_large_model_ids_and_exact_decimal_prices_without_personal_data():
    now = int(datetime.now(UTC).timestamp())
    raw = raw_order()
    projected = probe.project(raw, "123", now - 86400, now - 3)
    assert "PRIVATE" not in json.dumps(projected)
    item = projected["items"][0]
    assert item["listing_id"] == "9876543210123456789" and item["variation_id"] == "456"
    assert item["unit_price"] == {"amount": "10.125", "currency": "BRL"}
    raw["item_list"][0]["model_sku"] = ""
    assert probe.project(raw, "123", now - 86400, now - 3)["items"][0]["seller_sku"] is None
    simple = probe.project(raw_order(model=0), "123", now - 86400, now - 3)["items"][0]
    assert simple["variation_id"] == "0" and simple["seller_sku"] == "PARENT-SKU"


def test_shopee_query_signature_is_bound_to_fixed_read_paths_and_never_accepts_mutation_fields():
    query = {"order_sn_list": "261105ABC123", "response_optional_fields": probe.OPTIONAL}
    got = probe.parameters(
        probe.DETAIL, CREDS, query, now=1700000000, partner_id="", partner_key=""
    )
    expected = hmac.new(
        b"PRIVATE-KEY",
        b"456/api/v2/order/get_order_detail1700000000PRIVATE-TOKEN123",
        hashlib.sha256,
    ).hexdigest()
    assert got["sign"] == expected and got["shop_id"] == "123"
    for path, data in [
        ("/api/v2/order/cancel_order", query),
        (probe.DETAIL, {**query, "message": "x"}),
    ]:
        with pytest.raises(probe.ReadError):
            probe.parameters(path, CREDS, data, now=1, partner_id="", partner_key="")


def test_shopee_list_and_detail_have_two_gets_and_no_refresh_or_extra_optional_pii_fields():
    calls = []

    async def check():
        def handler(request):
            calls.append(request)
            if request.url.path == probe.LIST:
                return httpx.Response(
                    200,
                    json={
                        "error": "",
                        "response": {"order_list": [{"order_sn": "261105ABC123"}], "more": False},
                    },
                )
            raw = raw_order()
            raw["total_amount"] = 20.25
            raw["item_list"][0]["model_discounted_price"] = 10.125
            return httpx.Response(200, json={"error": "", "response": {"order_list": [raw]}})

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=False
        ) as client:
            result = await probe.read_account(
                client, CREDS, partner_id="", partner_key="", now=datetime.now(UTC)
            )
        assert result["state"] == "read_ok" and "PRIVATE" not in json.dumps(result)
        assert result["orders"][0]["total"]["amount"] == "20.25"

    asyncio.run(check())
    assert [r.method for r in calls] == ["GET", "GET"]
    assert [r.url.path for r in calls] == [probe.LIST, probe.DETAIL]
    assert "buyer" not in calls[1].url.params["response_optional_fields"]
    assert all(not r.content for r in calls)


def test_missing_order_detail_is_partial_read_and_not_a_false_empty_success():
    async def check():
        def handler(request):
            response = (
                {"order_list": [{"order_sn": "261105ABC123"}], "more": False}
                if request.url.path == probe.LIST
                else {"order_list": []}
            )
            return httpx.Response(200, json={"error": "", "response": response})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await probe.read_account(
                client, CREDS, partner_id="", partner_key="", now=datetime.now(UTC)
            )

    result = asyncio.run(check())
    assert (
        result["state"] == "partial_read"
        and result["missing_details"] == 1
        and result["has_more"] is False
        and result["pages_read"] == 1
    )


def test_pagination_keeps_the_same_window_and_opaque_cursor_on_the_api_host():
    calls = []

    async def check():
        def handler(request):
            calls.append(request)
            if request.url.path == probe.LIST:
                second = request.url.params["cursor"] == "PRIVATE-CURSOR"
                response = {
                    "order_list": [{"order_sn": "SECOND" if second else "FIRST"}],
                    "more": not second,
                    "next_cursor": "" if second else "PRIVATE-CURSOR",
                }
            else:
                raw = raw_order(request.url.params["order_sn_list"])
                raw["total_amount"] = "20.25"
                raw["item_list"][0]["model_discounted_price"] = "10.125"
                response = {"order_list": [raw]}
            return httpx.Response(200, json={"error": "", "response": response})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await probe.read_account(
                client, CREDS, partner_id="", partner_key="", now=datetime.now(UTC)
            )
        assert result["pages_read"] == 2 and result["has_more"] is False
        assert [o["id"] for o in result["orders"]] == ["FIRST", "SECOND"]
        assert "PRIVATE" not in json.dumps(result)

    asyncio.run(check())
    lists = [r.url.params for r in calls if r.url.path == probe.LIST]
    assert lists[0]["time_from"] == lists[1]["time_from"]
    assert lists[0]["time_to"] == lists[1]["time_to"]
    assert all(r.method == "GET" and not r.content for r in calls)


@pytest.mark.parametrize("next_cursor", [None, "", "\nPRIVATE", 12])
def test_more_pages_without_a_usable_cursor_never_claims_complete(next_cursor):
    async def check():
        def handler(request):
            response = (
                {"order_list": [{"order_sn": "FIRST"}], "more": True, "next_cursor": next_cursor}
                if request.url.path == probe.LIST
                else {"order_list": []}
            )
            return httpx.Response(200, json={"error": "", "response": response})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(probe.ReadError, match="invalid_response"):
                await probe.read_account(
                    client, CREDS, partner_id="", partner_key="", now=datetime.now(UTC)
                )

    asyncio.run(check())


def test_page_limit_is_explicit_and_duplicate_pages_are_refused(monkeypatch):
    monkeypatch.setattr(probe, "MAX_PAGES", 2)

    async def check(duplicate):
        page = 0

        def handler(request):
            nonlocal page
            if request.url.path == probe.LIST:
                page += 1
                response = {
                    "order_list": [{"order_sn": "FIRST" if duplicate else str(page)}],
                    "more": True,
                    "next_cursor": f"PRIVATE-{page}",
                }
            else:
                response = {"order_list": []}
            return httpx.Response(200, json={"error": "", "response": response})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            if duplicate:
                with pytest.raises(probe.ReadError, match="invalid_response"):
                    await probe.read_account(
                        client, CREDS, partner_id="", partner_key="", now=datetime.now(UTC)
                    )
            else:
                result = await probe.read_account(
                    client, CREDS, partner_id="", partner_key="", now=datetime.now(UTC)
                )
                assert result["pages_read"] == 2 and result["has_more"] is True
                assert result["missing_details"] == 2
        assert page == 2

    asyncio.run(check(False))
    asyncio.run(check(True))


def test_paginated_reports_remain_replayable_and_enforce_page_capacity(erp):
    known(erp)
    read = report()
    read.update(version=2, limit_per_account=probe.LIMIT)
    read["accounts"][0]["pages_read"] = 2
    native_orders.import_read(erp, read)
    assert native_orders.import_read(erp, read)["replayed"]
    for pages, missing in [(0, 0), (11, 0), (1, 100)]:
        bad = copy.deepcopy(read)
        bad["accounts"][0].update(
            pages_read=pages,
            missing_details=missing,
            state="partial_read" if missing else "read_ok",
        )
        with pytest.raises(ERPError):
            native_orders.import_read(erp, bad)


@pytest.mark.parametrize(
    "status,state", [(401, "auth_denied"), (429, "rate_limited"), (302, "provider_unavailable")]
)
def test_shopee_failures_do_not_follow_redirects_retry_or_expose_raw_errors(status, state):
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
            with pytest.raises(probe.ReadError) as error:
                await probe.read_account(
                    client, CREDS, partner_id="", partner_key="", now=datetime.now(UTC)
                )
            assert str(error.value) == state and "PRIVATE" not in str(error.value)

    asyncio.run(check())
    assert len(calls) == 1


def test_shopee_native_import_maps_listing_model_and_keeps_stock_and_active_sales_unchanged(erp):
    known(erp)
    read = report()
    before = erp.snapshot()
    assert native_orders.import_read(erp, read)["observations_added"] == 1
    assert native_orders.import_read(erp, read)["replayed"]
    with erp.connection() as db:
        db.execute(
            "INSERT INTO stock_routes VALUES "
            "('shopee-model','demo.kit','shopee',?,'Shopee local',"
            "'9876543210123456789','456','geral',1,'test')",
            (ACCOUNT,),
        )
        item = native_orders.snapshot(db)["orders"][0]["local_items"][0]
        assert item["sku"] == "demo.kit" and item["state"] == "matched"
        db.execute("UPDATE stock_routes SET variation_id='0'")
        assert native_orders.snapshot(db)["orders"][0]["local_items"][0]["state"] == "missing"
    after = erp.snapshot()
    for key in ["orders", "accounts", "movements", "products"]:
        assert after[key] == before[key]


@pytest.mark.parametrize("field", ["access_token", "buyer_user_id", "recipient_address"])
def test_shopee_report_refuses_unprojected_fields_before_any_local_write(erp, field):
    known(erp)
    read = report()
    read["accounts"][0]["orders"][0][field] = "PRIVATE"
    before = erp.snapshot()
    with pytest.raises(ERPError):
        native_orders.import_read(erp, read)
    assert erp.snapshot() == before


def test_shopee_partial_read_projection_remains_replayable_and_explicit(erp):
    known(erp)
    read = copy.deepcopy(report())
    read["accounts"][0].update(state="partial_read", missing_details=1, has_more=True)
    native_orders.import_read(erp, read)
    with erp.connection() as db:
        saved = native_orders.snapshot(db)["accounts"][0]
        assert saved["missing_details"] == 1 and saved["state"] == "partial_read"
