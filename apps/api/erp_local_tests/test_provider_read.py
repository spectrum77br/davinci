import copy
from datetime import UTC, datetime, timedelta

import pytest
from test_channels import routes

from app.erp_local.core import ERPError
from app.erp_local.provider_read import import_ml_read


def read():
    now = datetime.now(UTC)
    stamp = (now - timedelta(minutes=1)).isoformat()
    return {
        "source": "ml_api_read",
        "integration_id": "store-uuid",
        "started_at": now.isoformat(),
        "captured_at": now.isoformat(),
        "date_from": stamp,
        "date_to": now.isoformat(),
        "db_read_only": "on",
        "no_token_refresh": True,
        "limit": 20,
        "total_eligible": 1,
        "calls": [{"method": "GET", "path": "/orders/search", "status": 200}],
        "failures": [],
        "orders": [
            {
                "id": "123",
                "status": "paid",
                "created_at": stamp,
                "updated_at": stamp,
                "currency": "BRL",
                "shipment": {"status": "ready_to_ship", "logistic_type": "cross_docking"},
                "items": [
                    {
                        "listing_id": "MLB2",
                        "variation_id": "123",
                        "quantity": "1",
                        "unit_price": "3.4567",
                    }
                ],
            }
        ],
    }


def test_direct_ml_read_reserves_own_kit_using_exact_account_listing_and_variation(erp):
    routes(erp)
    data = read()
    report = import_ml_read(erp, data)
    assert report["results"][0]["status"] == "processed"
    assert import_ml_read(erp, data) == report
    state = erp.snapshot()
    assert len(state["orders"]) == 1
    assert state["orders"][0]["data"]["items"][0]["unit_price"] == "3.4567"
    assert state["orders"][0]["data"]["requirements"] == {
        "demo.aparelho": "1.000",
        "demo.fone": "1.000",
    }


@pytest.mark.parametrize(
    "change", ["variation", "account", "currency", "status", "fulfillment", "shipped"]
)
def test_unverified_provider_facts_are_held_without_reservation(erp, change):
    routes(erp)
    data = read()
    order = data["orders"][0]
    if change == "variation":
        order["items"][0]["variation_id"] = "wrong"
    elif change == "account":
        data["integration_id"] = "another-account"
    elif change == "currency":
        order["currency"] = "USD"
    elif change == "status":
        order["status"] = "partially_refunded"
    elif change == "fulfillment":
        order["shipment"]["logistic_type"] = "fulfillment"
    else:
        order["shipment"]["status"] = "shipped"
    assert import_ml_read(erp, data)["results"][0]["status"] == "review"
    assert not erp.snapshot()["orders"]


def test_extra_provider_personal_or_token_fields_are_rejected_before_any_write(erp):
    for place in ("root", "order", "item"):
        data = read()
        target = (
            data
            if place == "root"
            else data["orders"][0]
            if place == "order"
            else data["orders"][0]["items"][0]
        )
        target["buyer"] = {"address": "synthetic"}
        with pytest.raises(ERPError):
            import_ml_read(erp, data)
    assert not erp.snapshot()["orders"]


def test_projection_is_validated_in_full_before_first_receipt(erp):
    data = read()
    malformed = copy.deepcopy(data["orders"][0])
    malformed["id"] = "456"
    malformed["items"][0]["quantity"] = "NaN"
    data["orders"].append(malformed)
    with pytest.raises(ERPError):
        import_ml_read(erp, data)
    assert not erp.snapshot()["orders"]


def test_provider_search_result_outside_requested_window_has_no_effect(erp):
    routes(erp)
    data = read()
    data["orders"][0]["updated_at"] = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    assert import_ml_read(erp, data)["results"][0]["code"] == "outside_requested_window"
    assert not erp.snapshot()["orders"]
