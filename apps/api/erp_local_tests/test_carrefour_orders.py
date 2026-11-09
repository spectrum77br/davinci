import asyncio
import copy
import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from app.erp_local import carrefour_order_probe as probe
from app.erp_local import native_orders
from app.erp_local.core import ERPError, dumps

ACCOUNT = "de471c7c-f270-48ad-9ed1-878ce78e7453"
CREDS = {"shop_id": "123", "api_key": "PRIVATE-KEY"}


def raw_order(oid="ORDER-1-A"):
    updated = datetime.now(UTC) - timedelta(hours=1)
    return {
        "order_id": oid,
        "created_date": (updated - timedelta(hours=1)).isoformat(),
        "last_updated_date": updated.isoformat(),
        "order_state": "SHIPPING",
        "currency_iso_code": "BRL",
        "order_tax_mode": "TAX_INCLUDED",
        "total_price": Decimal("20.25"),
        "customer": {"firstname": "PRIVATE"},
        "customer_notification_email": "PRIVATE-EMAIL",
        "fulfillment": {"center": {"code": "CENTER"}},
        "order_lines": [
            {
                "order_line_id": oid + "-1",
                "offer_id": 9876543210123456789,
                "offer_sku": "demo.kit",
                "quantity": 2,
                "price_unit": Decimal("10.125"),
                "order_line_state": "SHIPPING",
                "product_title": "PRIVATE-TITLE",
            }
        ],
    }


def report():
    now = datetime.now(UTC)
    start = (now - timedelta(hours=24)).isoformat()
    end = (now - timedelta(seconds=3)).isoformat()
    return {
        "version": 1,
        "source": "carrefour_orders_or11_read",
        "read_only": "on",
        "no_token_refresh": True,
        "external_business_writes": False,
        "limit_per_account": probe.LIMIT,
        "captured_at": now.isoformat(),
        "accounts": [
            {
                "integration_id": ACCOUNT,
                "platform": "carrefour",
                "credentials_revision": (now - timedelta(hours=2)).isoformat(),
                "checked_at": now.isoformat(),
                "state": "read_ok",
                "http_status": 200,
                "orders": [probe.project(raw_order(), "123", start, end)],
                "has_more": False,
                "pages_read": 1,
                "total_eligible": 1,
                "coverage_review": False,
                "shop_id": "123",
                "identity_match": None,
                "identity_basis": "configured_shop_parameter",
                "identity_checked_at": None,
                "date_from": start,
                "date_to": end,
                "failure_stage": None,
            }
        ],
    }


def known(erp):
    with erp.connection() as db:
        db.execute(
            "INSERT INTO store_policies VALUES ('carrefour',?,'identity_review',?,?)",
            (ACCOUNT, dumps({"name": "Carrefour local"}), erp.now()),
        )


def test_carrefour_projection_keeps_offer_id_quantities_tax_mode_and_exact_unit_price_without_pii():
    now = datetime.now(UTC)
    got = probe.project(raw_order(), "123", (now - timedelta(days=1)).isoformat(), now.isoformat())
    assert "PRIVATE" not in json.dumps(got)
    assert got["items"][0]["listing_id"] == "9876543210123456789"
    assert got["items"][0]["variation_id"] is None and got["items"][0]["quantity"] == 2
    assert got["items"][0]["unit_price"] == {"amount": "10.125", "currency": "BRL"}
    assert got["tax_mode"] == "TAX_INCLUDED" and got["fulfilled_by"] is None


def test_prior_identity_cache_must_match_the_exact_key_shop_and_daily_window():
    now = int(datetime.now(UTC).timestamp())
    credentials = {
        **CREDS,
        "_carrefour_account_check": {
            "fingerprint": hashlib.sha256(b"123\0PRIVATE-KEY").hexdigest(),
            "checked_at": now - 60,
            "ok": True,
            "info": {"shop_id": 123, "shop_state": "OPEN"},
        },
    }
    got = probe.identity_cache(credentials, now=now)
    assert got["identity_match"] is True and got["identity_basis"] == "server_daily_cache"
    assert "PRIVATE" not in json.dumps(got)
    assert (
        probe.identity_cache({**credentials, "api_key": "OTHER"}, now=now)["identity_match"] is None
    )
    credentials["_carrefour_account_check"]["checked_at"] = now - 86400
    assert probe.identity_cache(credentials, now=now)["identity_match"] is None


def test_or11_pagination_uses_returned_page_size_without_a01_or_key_refresh():
    calls = []

    async def check():
        def handler(request):
            calls.append(request)
            offset = int(request.url.params["offset"])
            row = raw_order("ORDER-" + str(offset))
            row["total_price"] = "20.25"
            row["order_lines"][0]["price_unit"] = "10.125"
            return httpx.Response(200, json={"orders": [row], "total_count": 2})

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=False
        ) as client:
            result = await probe.read_account(client, CREDS, now=datetime.now(UTC))
        assert (
            result["state"] == "read_ok"
            and len(result["orders"]) == 2
            and result["pages_read"] == 2
        )
        assert result["identity_match"] is None and "PRIVATE" not in json.dumps(result)

    asyncio.run(check())
    assert [(r.method, r.url.path) for r in calls] == [
        ("GET", "/api/orders"),
        ("GET", "/api/orders"),
    ]
    assert [r.url.params["offset"] for r in calls] == ["0", "1"]
    assert calls[0].url.params["start_update_date"] == calls[1].url.params["start_update_date"]
    assert all(
        r.url.params["shop_id"] == "123"
        and r.url.params["order_tax_mode"] == "TAX_INCLUDED"
        and not r.content
        for r in calls
    )


@pytest.mark.parametrize(
    "status,state", [(401, "auth_denied"), (429, "rate_limited"), (302, "provider_unavailable")]
)
def test_carrefour_failure_never_retries_redirects_calls_a01_or_emits_raw_errors(status, state):
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
            with pytest.raises(probe.ReadError, match=state):
                await probe.read_account(client, CREDS, now=datetime.now(UTC))

    asyncio.run(check())
    assert len(calls) == 1


def test_missing_rows_before_total_is_partial_and_never_false_complete():
    async def check():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(200, json={"orders": [], "total_count": 2})
            )
        ) as client:
            return await probe.read_account(client, CREDS, now=datetime.now(UTC))

    result = asyncio.run(check())
    assert result["state"] == "partial_read" and result["coverage_review"] and result["has_more"]


def test_carrefour_native_import_matches_only_same_account_offer_and_null_variation_without_effects(
    erp,
):
    known(erp)
    read = report()
    before = erp.snapshot()
    assert native_orders.import_read(erp, read)["observations_added"] == 1
    assert native_orders.import_read(erp, read)["replayed"]
    with erp.connection() as db:
        db.execute(
            "INSERT INTO stock_routes VALUES ('offer','demo.kit','carrefour',?,'Carrefour local',"
            "'9876543210123456789',NULL,'geral',1,'test')",
            (ACCOUNT,),
        )
        assert native_orders.snapshot(db)["orders"][0]["local_items"][0]["sku"] == "demo.kit"
        db.execute("UPDATE stock_routes SET variation_id='0'")
        assert native_orders.snapshot(db)["orders"][0]["local_items"][0]["state"] == "missing"
    after = erp.snapshot()
    for key in ["products", "movements", "orders", "accounts"]:
        assert after[key] == before[key]


@pytest.mark.parametrize("field", ["customer", "api_key", "shipping_tracking_url"])
def test_unprojected_fields_are_refused_before_any_carrefour_import(erp, field):
    known(erp)
    read = report()
    read["accounts"][0]["orders"][0][field] = "PRIVATE"
    before = erp.snapshot()
    with pytest.raises(ERPError):
        native_orders.import_read(erp, read)
    assert erp.snapshot() == before


def test_prior_cache_cannot_be_labeled_as_a_fresh_identity_check(erp):
    known(erp)
    read = copy.deepcopy(report())
    read["accounts"][0]["identity_match"] = True
    with pytest.raises(ERPError):
        native_orders.import_read(erp, read)
    read["accounts"][0].update(
        identity_basis="server_daily_cache",
        identity_checked_at=read["accounts"][0]["credentials_revision"],
    )
    native_orders.import_read(erp, read)
