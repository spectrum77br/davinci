from datetime import UTC, datetime, timedelta

from test_channels import event
from test_reference import capture

from app.erp_local.catalog_repair import repair_missing_catalog
from app.erp_local.channels import ChannelFlow
from app.erp_local.core import LocalERP
from app.erp_local.initial_load import bootstrap_real_catalog
from app.erp_local.reference import ReferenceStore


def setup(tmp_path):
    erp = LocalERP(tmp_path / "repair.sqlite3")
    reference = ReferenceStore(erp)
    data = capture()
    data["products"][0].update(saldo_fisico=None, saldo_virtual_total=None)
    data["components"] = []
    reference.replace(data)
    bootstrap_real_catalog(erp, reference)
    now = datetime.now(UTC).isoformat()
    read = {
        "source": "bling_api_read",
        "started_at": now,
        "captured_at": now,
        "db_read_only": "on",
        "no_token_refresh": True,
        "balances": [
            {"id": "1", "physical": "10", "available": "7", "deposits": []},
            {"id": "2", "physical": "999", "available": "999", "deposits": []},
        ],
        "deposits": [],
        "structures": [
            {
                "id": "3",
                "sku": "real.kit",
                "name": "Kit",
                "format": "E",
                "status": "A",
                "components": [{"id": "1", "quantity": "2"}, {"id": "2", "quantity": "1"}],
            }
        ],
        "calls": [{"method": "GET", "path": "/estoques/saldos", "status": 200}],
        "failures": [],
    }
    return erp, read


def test_complete_only_missing_values_without_resetting_working_orders_or_balances(tmp_path):
    erp, read = setup(tmp_path)
    order = erp.command(
        "local",
        "order.import",
        {
            "channel": "local",
            "store": "demo",
            "external_id": "1",
            "depot": "geral",
            "items": [{"sku": "real.earphone", "quantity": "1", "unit_price": "5"}],
        },
    )
    result = repair_missing_catalog(erp, read)
    assert result["balances_completed"] == ["real.phone"]
    assert result["structures_completed"] == ["real.kit"]
    assert repair_missing_catalog(erp, read)["replayed"]
    snapshot = erp.snapshot()
    products = {p["sku"]: p for p in snapshot["products"]}
    assert (
        next(b for b in products["real.phone"]["balances"] if b["depot"] == "geral")["reserved"]
        == "3"
    )
    assert (
        next(b for b in products["real.earphone"]["balances"] if b["depot"] == "geral")["physical"]
        == "8"
    )
    assert (
        next(b for b in products["real.earphone"]["balances"] if b["depot"] == "geral")["reserved"]
        == "3.000"
    )
    assert snapshot["orders"][0]["id"] == order["id"]
    assert not snapshot["catalog_import"]["issues"]


def test_unresolved_component_identity_is_not_guessed_from_the_sku(tmp_path):
    erp, read = setup(tmp_path)
    read["structures"][0]["components"][0]["id"] = "unknown"
    result = repair_missing_catalog(erp, read)
    assert not result["structures_completed"]
    assert result["blocked"][0]["code"] == "component_identity"
    assert erp.snapshot()["catalog_import"]["issues"]


def test_order_predating_recovered_stock_is_held_for_reconciliation(tmp_path):
    erp, read = setup(tmp_path)
    future_opening = datetime.now(UTC) + timedelta(minutes=1)
    read["captured_at"] = future_opening.isoformat()
    repair_missing_catalog(erp, read)
    payload = event(sku="real.phone")
    payload["order_created_at"] = datetime.now(UTC).isoformat()
    payload["occurred_at"] = payload["order_created_at"]
    result = ChannelFlow(erp).receive(payload, source="provider_read")
    assert (
        result["status"] == "review" and result["outcome"]["code"] == "opening_reservation_review"
    )
    assert not erp.snapshot()["orders"]
