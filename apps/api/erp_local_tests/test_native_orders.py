import asyncio
import copy
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.erp_local import amazon_order_probe as probe
from app.erp_local import native_orders
from app.erp_local.core import ERPError, dumps

ACCOUNT = "89acc64b-bc00-4840-87cb-2248105e201b"


def read_plan():
    return probe.plan(
        {"access_token": "PRIVATE-TOKEN", "marketplace_id": probe.MARKETPLACE}, datetime.now(UTC)
    )


def body():
    updated = datetime.now(UTC) - timedelta(hours=1)
    return {
        "orders": [
            {
                "orderId": "701-1234567-1234567",
                "createdTime": (updated - timedelta(hours=1)).isoformat(),
                "lastUpdatedTime": updated.isoformat(),
                "salesChannel": {"marketplaceId": probe.MARKETPLACE},
                "fulfillment": {"fulfillmentStatus": "UNSHIPPED", "fulfilledBy": "MERCHANT"},
                "proceeds": {"grandTotal": {"amount": "20.25", "currencyCode": "BRL"}},
                "buyer": {"buyerName": "PRIVATE-BUYER", "buyerEmail": "PRIVATE-EMAIL"},
                "recipient": {"deliveryAddress": {"addressLine1": "PRIVATE-ADDRESS"}},
                "payment": {"secret": "PRIVATE-PAYMENT"},
                "tax": {"cpf": "PRIVATE-TAX"},
                "orderItems": [
                    {
                        "orderItemId": "line-1",
                        "quantityOrdered": 2,
                        "product": {
                            "sellerSku": "demo.kit",
                            "asin": "B012345678",
                            "title": "PRIVATE-TITLE",
                            "serialNumbers": ["PRIVATE-SERIAL"],
                            "price": {"unitPrice": {"amount": "10.1250", "currencyCode": "BRL"}},
                        },
                        "proceeds": {"proceedsTotal": {"amount": "20.25", "currencyCode": "BRL"}},
                    }
                ],
            }
        ],
        "pagination": {"nextToken": "PRIVATE-PAGE-TOKEN"},
    }


def report():
    p = read_plan()
    now = datetime.now(UTC)
    return {
        "version": 2,
        "source": "amazon_orders_2026_read",
        "read_only": "on",
        "no_token_refresh": True,
        "external_business_writes": False,
        "captured_at": now.isoformat(),
        "limit_per_account": probe.LIMIT,
        "accounts": [
            {
                "integration_id": ACCOUNT,
                "platform": "amazon",
                "credentials_revision": (now - timedelta(hours=1)).isoformat(),
                "checked_at": now.isoformat(),
                "date_from": p.date_from,
                "date_to": p.date_to,
                "state": "read_ok",
                "http_status": 200,
                **probe.project(body(), p),
            }
        ],
    }


def known_account(erp):
    with erp.connection() as db:
        db.execute(
            "INSERT INTO store_policies VALUES ('amazon',?,'identity_review',?,?)",
            (ACCOUNT, dumps({"name": "Amazon de demonstração"}), erp.now()),
        )


def test_current_amazon_plan_uses_only_documented_2026_read_and_excludes_pii_datasets():
    p = read_plan()
    assert p.url == probe.BASE + probe.PATH and "/v0/" not in p.url
    assert "PRIVATE" not in repr(p)
    assert p.params["maxResultsPerPage"] == 100
    assert set(p.params["includedData"].split(",")) == {"FULFILLMENT", "PROCEEDS"}
    assert (datetime.now(UTC) - probe.stamp(p.date_to)).total_seconds() >= 180
    with pytest.raises(probe.OrderReadError, match="configuration"):
        probe.plan(
            {"access_token": "PRIVATE", "marketplace_id": "another-region"}, datetime.now(UTC)
        )


def test_projection_keeps_exact_prices_and_ids_but_drops_personal_data_and_page_tokens():
    p = read_plan()
    projected = probe.project(body(), p)
    assert "PRIVATE" not in json.dumps(projected)
    item = projected["orders"][0]["items"][0]
    assert item["unit_price"] == {"amount": "10.1250", "currency": "BRL"}
    assert item["quantity"] == 2 and item["seller_sku"] == "demo.kit"
    assert projected["has_more"] is True and projected["orders"][0]["within_window"] is True
    raw = body()
    raw["orders"][0]["orderItems"][0]["product"].pop("price")
    assert probe.project(raw, p)["orders"][0]["items"][0]["unit_price"] is None


@pytest.mark.parametrize(
    "status,state",
    [
        (401, "auth_denied"),
        (403, "auth_denied"),
        (429, "rate_limited"),
        (302, "provider_unavailable"),
    ],
)
def test_order_read_has_one_get_no_retry_refresh_redirect_or_error_body_leak(status, state):
    calls = []

    async def run():
        def handler(request):
            calls.append(request)
            return httpx.Response(
                status,
                json={"message": "PRIVATE-TOKEN"},
                headers={"Location": "https://evil.invalid"},
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=False
        ) as client:
            result = await probe.execute(read_plan(), client)
        assert result["state"] == state and "PRIVATE" not in json.dumps(result)

    asyncio.run(run())
    assert len(calls) == 1 and calls[0].method == "GET" and not calls[0].content
    with pytest.raises(probe.OrderReadError):
        asyncio.run(probe.execute(replace(read_plan(), url="https://evil.invalid/orders"), None))


def test_http_200_does_not_accept_legacy_payload_or_wrong_marketplace():
    async def check(raw):
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=raw))
        ) as client:
            return await probe.execute(read_plan(), client)

    assert asyncio.run(check({"payload": {"Orders": []}}))["state"] == "invalid_response"
    raw = body()
    raw["orders"][0]["salesChannel"]["marketplaceId"] = "US"
    assert asyncio.run(check(raw))["state"] == "marketplace_mismatch"
    raw = body()
    raw["orders"][0]["orderItems"][0]["quantityOrdered"] = True
    assert asyncio.run(check(raw))["state"] == "invalid_response"


def test_import_is_idempotent_and_never_creates_orders_reservations_or_accounts(erp):
    known_account(erp)
    read = report()
    before = erp.snapshot()
    result = native_orders.import_read(erp, read)
    assert result["observations_added"] == 1 and result["active_orders_created"] == 0
    assert native_orders.import_read(erp, read)["replayed"]
    after = erp.snapshot()
    for key in ["orders", "accounts", "movements", "products"]:
        assert after[key] == before[key]
    with erp.connection() as db:
        snapshot = native_orders.snapshot(db)
    assert len(snapshot["orders"]) == 1 and snapshot["active_processing"] is False
    assert snapshot["accounts"][0]["has_more"] is True


def test_same_remote_revision_with_changed_content_is_retained_as_a_conflict(erp):
    known_account(erp)
    first = report()
    native_orders.import_read(erp, first)
    second = copy.deepcopy(first)
    second["captured_at"] = datetime.now(UTC).isoformat()
    second["accounts"][0]["orders"][0]["total"]["amount"] = "21.00"
    native_orders.import_read(erp, second)
    with erp.connection() as db:
        assert db.execute("SELECT count(*) FROM native_order_observations").fetchone()[0] == 2
        head = native_orders.snapshot(db)["orders"][0]
        assert head["revision_conflict"] is True
    assert not erp.snapshot()["orders"]


def test_original_two_order_read_remains_replayable_after_larger_batch_version(erp):
    known_account(erp)
    historical = report()
    historical.update(version=1, limit_per_account=2)
    assert native_orders.import_read(erp, historical)["observations_added"] == 1
    assert native_orders.import_read(erp, historical)["replayed"]


def test_native_item_identity_requires_the_exact_account_seller_sku_and_asin(erp):
    known_account(erp)
    native_orders.import_read(erp, report())
    with erp.connection() as db:
        assert native_orders.snapshot(db)["orders"][0]["local_items"][0]["state"] == "missing"
        db.execute(
            "INSERT INTO stock_routes VALUES "
            "('amz-exact','demo.kit','amazon',?,'Amazon local','demo.kit',"
            "'B012345678','geral',1,'test')",
            (ACCOUNT,),
        )
        item = native_orders.snapshot(db)["orders"][0]["local_items"][0]
        assert item == {"line_id": "line-1", "state": "matched", "sku": "demo.kit", "kind": "kit"}
        db.execute("UPDATE stock_routes SET variation_id='another-ASIN'")
        assert native_orders.snapshot(db)["orders"][0]["local_items"][0]["state"] == "missing"
        db.execute("UPDATE stock_routes SET variation_id='B012345678'")
        db.execute(
            "INSERT INTO stock_routes VALUES "
            "('amz-ambiguous','demo.fone','amazon',?,'Amazon local','demo.kit',"
            "'B012345678','geral',1,'test')",
            (ACCOUNT,),
        )
        assert native_orders.snapshot(db)["orders"][0]["local_items"][0]["state"] == "ambiguous"


def test_projection_failure_reports_only_a_fixed_stage_never_remote_values():
    raw = body()
    raw["orders"][0]["proceeds"]["grandTotal"]["amount"] = "PRIVATE-BAD-AMOUNT"

    async def check():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=raw))
        ) as client:
            return await probe.execute(read_plan(), client)

    result = asyncio.run(check())
    assert result["state"] == "invalid_response" and result["failure_stage"] == "money"
    assert "PRIVATE" not in json.dumps(result) and result["orders"] == []


def test_version_three_failure_preserves_the_failed_read_without_fake_empty_success(erp):
    known_account(erp)
    read = report()
    read["version"] = 3
    account = read["accounts"][0]
    account.update(state="invalid_response", failure_stage="quantity", orders=[], has_more=None)
    result = native_orders.import_read(erp, read)
    assert result["observations_added"] == 0
    with erp.connection() as db:
        saved = native_orders.snapshot(db)["accounts"][0]
        assert saved["state"] == "invalid_response" and saved["failure_stage"] == "quantity"
    read["accounts"][0]["failure_stage"] = "PRIVATE-REMOTE-ERROR"
    with pytest.raises(ERPError):
        native_orders.import_read(erp, read)


def test_zero_quantity_from_provider_is_preserved_for_review_without_a_stock_instruction(erp):
    known_account(erp)
    raw = body()
    raw["orders"][0]["fulfillment"]["fulfillmentStatus"] = "CANCELLED"
    raw["orders"][0]["orderItems"][0]["quantityOrdered"] = 0
    p = read_plan()
    read = report()
    read["version"] = 4
    read["accounts"][0].update(probe.project(raw, p), failure_stage=None)
    before = erp.snapshot()["movements"]
    native_orders.import_read(erp, read)
    with erp.connection() as db:
        assert native_orders.snapshot(db)["orders"][0]["data"]["items"][0]["quantity"] == 0
    assert not erp.snapshot()["orders"] and erp.snapshot()["movements"] == before


@pytest.mark.parametrize(
    "change", ["secret", "false_window", "wrong_store", "unapproved_write", "invalid_money"]
)
def test_invalid_or_unbound_projection_rolls_back_all_observations(erp, change):
    known_account(erp)
    read = report()
    account = read["accounts"][0]
    if change == "secret":
        account["access_token"] = "PRIVATE"  # noqa: S105 - deliberate fake-field rejection
    elif change == "false_window":
        account["orders"][0]["within_window"] = False
    elif change == "wrong_store":
        account["integration_id"] = "781c37e7-1dc2-4aa5-a2b3-b3d9df880346"
    elif change == "unapproved_write":
        read["external_business_writes"] = True
    else:
        account["orders"][0]["total"]["amount"] = "NaN"
    before = erp.snapshot()
    with pytest.raises(ERPError):
        native_orders.import_read(erp, read)
    assert erp.snapshot() == before
    with erp.connection() as db:
        assert db.execute("SELECT count(*) FROM native_order_reads").fetchone()[0] == 0
