import asyncio
import copy
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.erp_local import magalu_order_probe as probe
from app.erp_local import native_orders
from app.erp_local.core import ERPError, dumps

ACCOUNT = "de471c7c-f270-48ad-9ed1-878ce78e7453"
DELIVERY = "e8dbb42a-7ddc-41a0-a626-b3aa27a09a1b"
PRODUCT = "2dc0121f-a85c-4bd7-af59-39b1b0572c20"
CREDS = {"access_token": "PRIVATE-TOKEN", "refresh_token": "PRIVATE-REFRESH"}


def raw_order(code="1234567890123456789"):
    now = datetime.now(UTC) - timedelta(hours=1)
    return {
        "id": "86f0f25d-29f7-4fd9-afec-cf5333f55d78",
        "code": code,
        "created_at": (now - timedelta(hours=1)).isoformat(),
        "updated_at": now.isoformat(),
        "status": "approved",
        "customer": {"document_number": "PRIVATE-DOCUMENT", "email": "PRIVATE-EMAIL"},
        "amounts": {"total": 2001, "normalizer": 100, "currency": "BRL"},
        "deliveries": [
            {
                "id": DELIVERY,
                "status": "approved",
                "seller": {"id": "PRIVATE-SELLER", "name": "PRIVATE-NAME"},
                "items": [
                    {
                        "info": {"id": PRODUCT, "sku": "demo-kit", "name": "PRIVATE-PRODUCT"},
                        "quantity": 2,
                        "unit_price": {"value": 10005, "normalizer": 1000, "currency": "BRL"},
                        "amounts": {"total": 2001, "normalizer": 100, "currency": "BRL"},
                    }
                ],
            }
        ],
    }


def report():
    now = datetime.now(UTC)
    return {
        "version": 1,
        "source": "magalu_orders_v1_read",
        "read_only": "on",
        "no_token_refresh": True,
        "external_business_writes": False,
        "captured_at": now.isoformat(),
        "limit_per_account": probe.LIMIT,
        "accounts": [
            {
                "integration_id": ACCOUNT,
                "platform": "magalu",
                "credentials_revision": (now - timedelta(hours=2)).isoformat(),
                "checked_at": now.isoformat(),
                "state": "read_ok",
                "http_status": 200,
                "orders": [probe.project(raw_order())],
                "has_more": False,
                "pages_read": 1,
                "coverage_scope": "bounded_unfiltered",
                "identity_match": None,
                "date_from": None,
                "date_to": None,
                "failure_stage": None,
            }
        ],
    }


def known(erp):
    with erp.connection() as db:
        db.execute(
            "INSERT INTO store_policies VALUES ('magalu',?,'identity_review',?,?)",
            (ACCOUNT, dumps({"name": "Magalu local"}), erp.now()),
        )


def test_magalu_projection_preserves_raw_seller_sku_delivery_and_explicit_normalizer():
    got = probe.project(raw_order())
    assert "PRIVATE" not in json.dumps(got)
    assert got["id"] == "1234567890123456789" and got["within_window"] is None
    assert got["fulfilled_by"] is None and got["total"]["amount"] == "20.01"
    item = got["items"][0]
    assert item["listing_id"] == item["seller_sku"] == "demo-kit"
    assert item["product_id"] == PRODUCT and item["delivery_id"] == DELIVERY
    assert item["variation_id"] is None and item["quantity"] == 2
    assert item["unit_price"]["amount"] == "10.005"


@pytest.mark.parametrize("bad", [None, True, -1, 1.5, "2"])
def test_magalu_never_infers_a_missing_or_noninteger_quantity(bad):
    raw = raw_order()
    raw["deliveries"][0]["items"][0]["quantity"] = bad
    with pytest.raises(probe.ReadError, match="invalid_response"):
        probe.project(raw)


@pytest.mark.parametrize("bad", [None, 0, 3, True, "100"])
def test_magalu_never_guesses_currency_scale(bad):
    raw = raw_order()
    raw["amounts"]["normalizer"] = bad
    with pytest.raises(probe.ReadError, match="invalid_response"):
        probe.project(raw)


def test_bounded_pagination_uses_fixed_endpoint_and_explicit_unfiltered_scope():
    calls = []

    async def check():
        def handler(request):
            calls.append(request)
            offset = int(request.url.params["_offset"])
            row = raw_order(str(offset))
            return httpx.Response(
                200,
                json={
                    "results": [row],
                    "meta": {
                        "links": {"next": "https://evil.invalid/PRIVATE" if offset == 0 else None},
                        "page": {"count": 1, "offset": offset},
                    },
                },
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=False
        ) as client:
            got = await probe.read_account(client, CREDS)
        assert got["state"] == "read_ok" and len(got["orders"]) == 2
        assert got["coverage_scope"] == "bounded_unfiltered" and got["pages_read"] == 2
        assert got["date_from"] is None and got["date_to"] is None and got["identity_match"] is None
        assert "PRIVATE" not in json.dumps(got)

    asyncio.run(check())
    assert [(r.method, str(r.url).split("?")[0]) for r in calls] == [("GET", probe.URL)] * 2
    assert [dict(r.url.params) for r in calls] == [
        {"_limit": "50", "_offset": "0"},
        {"_limit": "50", "_offset": "1"},
    ]
    assert all(not r.content for r in calls)


@pytest.mark.parametrize(
    "code,state", [(401, "auth_denied"), (429, "rate_limited"), (302, "provider_unavailable")]
)
def test_magalu_never_refreshes_tokens_retries_redirects_or_exports_error_bodies(code, state):
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
                await probe.read_account(client, CREDS)

    asyncio.run(check())
    assert len(calls) == 1 and calls[0].method == "GET"


def test_magalu_import_is_idempotent_and_resolves_exact_account_raw_sku_and_null_variation(erp):
    known(erp)
    read, before = report(), erp.snapshot()
    assert native_orders.import_read(erp, read)["observations_added"] == 1
    assert native_orders.import_read(erp, read)["replayed"]
    with erp.connection() as db:
        db.execute(
            "INSERT INTO stock_routes VALUES ('offer','demo.kit','magalu',?,'Magalu local',"
            "'demo-kit',NULL,'geral',1,'test')",
            (ACCOUNT,),
        )
        assert native_orders.snapshot(db)["orders"][0]["local_items"][0]["sku"] == "demo.kit"
        db.execute("UPDATE stock_routes SET external_id='demo.kit'")
        assert native_orders.snapshot(db)["orders"][0]["local_items"][0]["state"] == "missing"
        db.execute("UPDATE stock_routes SET external_id='demo-kit',variation_id='0'")
        assert native_orders.snapshot(db)["orders"][0]["local_items"][0]["state"] == "missing"
    after = erp.snapshot()
    for key in ["products", "movements", "orders", "accounts"]:
        assert after[key] == before[key]


@pytest.mark.parametrize("kind", ["pii", "window", "quantity", "identity"])
def test_magalu_contract_rejects_private_or_unproven_fields_before_any_import(erp, kind):
    known(erp)
    read = copy.deepcopy(report())
    account = read["accounts"][0]
    if kind == "pii":
        account["orders"][0]["customer"] = {"email": "PRIVATE"}
    elif kind == "window":
        account["date_from"] = account["checked_at"]
    elif kind == "quantity":
        account["orders"][0]["items"][0]["quantity"] = None
    else:
        account["identity_match"] = True
    before = erp.snapshot()
    with pytest.raises(ERPError):
        native_orders.import_read(erp, read)
    assert erp.snapshot() == before


def test_empty_magalu_page_with_continuation_cannot_claim_coverage():
    async def check():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(
                    200,
                    json={"results": [], "meta": {"links": {"next": "?page=2"}}},
                )
            )
        ) as client:
            with pytest.raises(probe.ReadError, match="invalid_response"):
                await probe.read_account(client, CREDS)

    asyncio.run(check())


def test_magalu_final_page_may_omit_next_as_observed_on_the_live_empty_account():
    async def check():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(
                    200,
                    json={
                        "results": [],
                        "meta": {"links": {"self": "PRIVATE"}, "page": {"count": 0, "offset": 0}},
                    },
                )
            )
        ) as client:
            got = await probe.read_account(client, CREDS)
            assert got["orders"] == [] and got["has_more"] is False
            assert got["pages_read"] == 1 and "PRIVATE" not in json.dumps(got)

    asyncio.run(check())
