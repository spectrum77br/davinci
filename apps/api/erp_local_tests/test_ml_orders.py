import asyncio
import copy
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from app.erp_local import ml_order_probe as probe
from app.erp_local import native_orders, native_reconciliation
from app.erp_local.core import ERPError, dumps

ACCOUNT = "de471c7c-f270-48ad-9ed1-878ce78e7453"
SELLER = "1234567890123456789"
CREDS = {"access_token": "PRIVATE-TOKEN", "refresh_token": "PRIVATE-REFRESH", "user_id": SELLER}


def raw_order(oid="2000011234567890"):
    now = datetime.now(UTC) - timedelta(hours=1)
    return {
        "id": oid,
        "seller": {"id": SELLER, "nickname": "PRIVATE-SELLER"},
        "pack_id": "2000001234567890",
        "shipping": {"id": 45123456789},
        "date_created": (now - timedelta(hours=1)).isoformat(),
        "date_last_updated": now.isoformat(),
        "status": "paid",
        "total_amount": Decimal("20.25"),
        "currency_id": "BRL",
        "buyer": {"id": "PRIVATE-BUYER", "email": "PRIVATE-EMAIL"},
        "order_items": [
            {
                "item": {
                    "id": "MLB123456789",
                    "variation_id": None,
                    "seller_sku": "demo.kit",
                    "seller_custom_field": "reference-local",
                    "title": "PRIVATE-PRODUCT",
                },
                "quantity": 2,
                "unit_price": Decimal("10.125"),
            }
        ],
    }


def report():
    now = datetime.now(UTC)
    start, end = (now - timedelta(hours=24)).isoformat(), (now - timedelta(seconds=3)).isoformat()
    return {
        "version": 1,
        "source": "ml_orders_v1_read",
        "read_only": "on",
        "no_token_refresh": True,
        "external_business_writes": False,
        "captured_at": now.isoformat(),
        "limit_per_account": probe.LIMIT,
        "accounts": [
            {
                "integration_id": ACCOUNT,
                "platform": "ml",
                "credentials_revision": (now - timedelta(hours=2)).isoformat(),
                "checked_at": now.isoformat(),
                "state": "read_ok",
                "http_status": 200,
                "orders": [probe.project(raw_order(), SELLER, start, end)],
                "has_more": False,
                "pages_read": 1,
                "total_eligible": 1,
                "coverage_review": False,
                "identity_match": True,
                "seller_id": SELLER,
                "date_from": start,
                "date_to": end,
                "failure_stage": None,
            }
        ],
    }


def known(erp):
    with erp.connection() as db:
        db.execute(
            "INSERT INTO store_policies VALUES ('ml',?,'shadow',?,?)",
            (ACCOUNT, dumps({"name": "ML local"}), erp.now()),
        )


def test_ml_projection_preserves_seller_listing_nullable_variation_pack_and_money_without_pii():
    read = report()
    native_orders.validate(read)
    assert "PRIVATE" not in json.dumps(read)
    order = read["accounts"][0]["orders"][0]
    assert order["seller_id"] == SELLER and order["pack_id"] == "2000001234567890"
    assert order["shipping_id"] == "45123456789" and order["fulfilled_by"] is None
    assert order["items"][0]["variation_id"] is None
    assert order["items"][0]["unit_price"] == {"amount": "10.125", "currency": "BRL"}


def test_ml_checks_exact_seller_before_search_and_rechecks_each_order():
    calls = []

    async def check():
        def handler(request):
            calls.append(request)
            return httpx.Response(200, json={"id": "999", "site_id": "MLB"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(probe.ReadError, match="identity_mismatch"):
                await probe.read_account(client, CREDS, now=datetime.now(UTC))

    asyncio.run(check())
    assert [r.url.path for r in calls] == ["/users/me"]
    read = report()
    raw = raw_order()
    raw["seller"]["id"] = "999"
    with pytest.raises(probe.ReadError, match="identity_mismatch"):
        probe.project(raw, SELLER, read["accounts"][0]["date_from"], read["accounts"][0]["date_to"])


def test_ml_pagination_keeps_fixed_window_and_uses_only_identity_and_order_gets():
    calls = []

    async def check():
        def handler(request):
            calls.append(request)
            if request.url.path == "/users/me":
                return httpx.Response(200, json={"id": SELLER, "site_id": "MLB"})
            offset = int(request.url.params["offset"])
            raw = json.loads(json.dumps(raw_order(str(2000011234567890 + offset)), default=str))
            return httpx.Response(
                200, json={"results": [raw], "paging": {"total": 2, "offset": offset, "limit": 50}}
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=False
        ) as client:
            got = await probe.read_account(client, CREDS, now=datetime.now(UTC))
        assert got["identity_match"] is True and got["pages_read"] == 2
        assert len(got["orders"]) == 2 and got["has_more"] is False
        assert "PRIVATE" not in json.dumps(got)

    asyncio.run(check())
    assert [r.url.path for r in calls] == ["/users/me", "/orders/search", "/orders/search"]
    assert all(r.method == "GET" and not r.content for r in calls)
    assert [r.url.params["offset"] for r in calls[1:]] == ["0", "1"]
    assert (
        calls[1].url.params["order.date_last_updated.from"]
        == calls[2].url.params["order.date_last_updated.from"]
    )
    assert calls[1].url.params["seller"] == SELLER


@pytest.mark.parametrize(
    "code,state", [(401, "auth_denied"), (429, "rate_limited"), (302, "provider_unavailable")]
)
def test_ml_does_not_refresh_retry_follow_redirects_or_export_raw_errors(code, state):
    calls = []

    async def check():
        def handler(request):
            calls.append(request)
            return httpx.Response(
                code, text="PRIVATE-ERROR", headers={"Location": "https://evil.invalid"}
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=False
        ) as client:
            with pytest.raises(probe.ReadError, match=state):
                await probe.read_account(client, CREDS, now=datetime.now(UTC))

    asyncio.run(check())
    assert len(calls) == 1 and calls[0].url.path == "/users/me"


def test_ml_import_is_idempotent_exact_account_and_listing_with_null_not_zero_variation(erp):
    known(erp)
    read, before = report(), erp.snapshot()
    assert native_orders.import_read(erp, read)["observations_added"] == 1
    assert native_orders.import_read(erp, read)["replayed"]
    with erp.connection() as db:
        db.execute(
            "INSERT INTO stock_routes VALUES ('offer','demo.kit','ml',?,'ML local',"
            "'MLB123456789',NULL,'geral',1,'test')",
            (ACCOUNT,),
        )
        assert native_orders.snapshot(db)["orders"][0]["local_items"][0]["sku"] == "demo.kit"
        db.execute("UPDATE stock_routes SET variation_id='0'")
        assert native_orders.snapshot(db)["orders"][0]["local_items"][0]["state"] == "missing"
        db.execute("UPDATE stock_routes SET variation_id=NULL,store='other'")
        assert native_orders.snapshot(db)["orders"][0]["local_items"][0]["state"] == "missing"
    after = erp.snapshot()
    for key in ["products", "movements", "orders", "accounts"]:
        assert after[key] == before[key]


def test_ml_pack_member_is_not_compared_as_if_it_were_the_entire_source_cart():
    order = report()["accounts"][0]["orders"][0]
    source = {
        "captured_at": "2026-11-05T00:00:00Z",
        "policies": {("ml", ACCOUNT): "B1"},
        "orders": {("B1", order["pack_id"]): [{"number": "300", "items": []}]},
    }
    got = native_reconciliation.compare("ml", ACCOUNT, order, [], source)
    assert got["state"] == "group_identity_review" and got["source_numbers"] == ["300"]
    assert got["operational_match"] is False


@pytest.mark.parametrize("field", ["buyer", "access_token", "payments"])
def test_ml_projection_refuses_unrequested_fields_before_import(erp, field):
    known(erp)
    read = copy.deepcopy(report())
    read["accounts"][0]["orders"][0][field] = "PRIVATE"
    before = erp.snapshot()
    with pytest.raises(ERPError):
        native_orders.import_read(erp, read)
    assert erp.snapshot() == before


def test_ml_quantity_is_never_inferred_from_a_line_or_a_pack():
    raw = raw_order()
    raw["order_items"][0]["quantity"] = None
    now = datetime.now(UTC)
    with pytest.raises(probe.ReadError, match="invalid_response"):
        probe.project(raw, SELLER, (now - timedelta(days=1)).isoformat(), now.isoformat())


def test_ml_orders_outside_the_requested_window_are_retained_with_coverage_review():
    async def check():
        now = datetime.now(UTC)
        raw = raw_order()
        raw.update(
            date_created=(now - timedelta(hours=26)).isoformat(),
            date_last_updated=(now - timedelta(hours=25)).isoformat(),
        )

        def handler(request):
            if request.url.path == "/users/me":
                return httpx.Response(200, json={"id": SELLER, "site_id": "MLB"})
            return httpx.Response(
                200,
                json={
                    "results": [json.loads(json.dumps(raw, default=str))],
                    "paging": {"total": 1, "offset": 0, "limit": 50},
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            got = await probe.read_account(client, CREDS, now=now)
        assert got["state"] == "partial_read" and got["coverage_review"]
        assert len(got["orders"]) == 1 and got["orders"][0]["within_window"] is False

    asyncio.run(check())
