from decimal import Decimal

import pytest
from test_core import balance, order, run

from app.erp_local import procurement, production
from app.erp_local.core import ERPError
from app.erp_local.release import inspect_database


def create(erp, **changes):
    if not any(p["sku"] == "physical.bundle" for p in erp.snapshot()["products"]):
        run(
            erp,
            "product.create",
            {
                "sku": "physical.bundle",
                "name": "Conjunto físico",
                "kind": "simple",
                "price": "100",
                "cost_price": "0",
            },
        )
    return run(
        erp,
        "production.save",
        {
            "reference": "OP-001",
            "sku": "physical.bundle",
            "quantity": "2",
            "components": [
                {"sku": "demo.aparelho", "quantity": "1"},
                {"sku": "demo.fone", "quantity": "1"},
            ],
            "origin": "geral",
            "destination": "geral",
            **changes,
        },
    )["id"]


def state(erp):
    with erp.connection() as db:
        return production.snapshot(db)


def deliver(erp, identifier, **changes):
    return run(
        erp,
        "production.deliver",
        {"id": identifier, "receipt_id": "batch-1", "quantity": "1", **changes},
    )


def test_planning_start_pause_partial_finish_and_reversals(erp):
    identifier = create(erp)
    assert balance(erp) == (8, 0, 8)
    run(erp, "production.start", {"id": identifier})
    assert balance(erp) == (8, 2, 6)
    run(erp, "production.pause", {"id": identifier})
    assert balance(erp) == (8, 2, 6)
    with pytest.raises(ERPError, match="Inicie"):
        deliver(erp, identifier)
    run(erp, "production.start", {"id": identifier})
    first = deliver(erp, identifier)["id"]
    assert balance(erp) == (7, 1, 6)
    assert balance(erp, "physical.bundle") == (1, 0, 1)
    second = deliver(erp, identifier, receipt_id="batch-2")["id"]
    assert state(erp)["orders"][0]["status"] == "finished"
    with pytest.raises(ERPError, match="recente"):
        run(erp, "production.reverse", {"id": first})
    run(erp, "production.reverse", {"id": second})
    assert state(erp)["orders"][0]["status"] == "planned"
    assert balance(erp) == (7, 0, 7)
    run(erp, "production.reverse", {"id": first})
    assert balance(erp) == (8, 0, 8)
    assert balance(erp, "physical.bundle") == (0, 0, 0)
    assert inspect_database(erp.database)["ledger_ok"]


def test_start_is_atomic_and_competes_with_sales_reservations(erp):
    identifier = create(erp, quantity="6")
    before = erp.snapshot()
    with pytest.raises(ERPError):
        run(erp, "production.start", {"id": identifier})
    assert erp.snapshot() == before
    identifier = create(erp, reference="OP-002", quantity="4")
    order(erp, quantity="2")
    before = erp.snapshot()
    with pytest.raises(ERPError):
        run(erp, "production.start", {"id": identifier})
    assert erp.snapshot() == before


def test_cancel_releases_only_outstanding_reservations_and_preserves_delivered_stock(erp):
    identifier = create(erp)
    run(erp, "production.start", {"id": identifier})
    posted = deliver(erp, identifier)["id"]
    run(erp, "production.cancel", {"id": identifier})
    assert balance(erp) == (7, 0, 7)
    assert balance(erp, "physical.bundle") == (1, 0, 1)
    run(erp, "production.reverse", {"id": posted})
    assert balance(erp) == (8, 0, 8)
    assert state(erp)["orders"][0]["status"] == "cancelled"


def test_delivery_identity_survives_reversal_and_never_double_produces(erp):
    identifier = create(erp)
    run(erp, "production.start", {"id": identifier})
    posted = deliver(erp, identifier)["id"]
    before = erp.snapshot()
    assert deliver(erp, identifier)["duplicate"]
    assert erp.snapshot()["movements"] == before["movements"]
    with pytest.raises(ERPError, match="outros dados"):
        deliver(erp, identifier, quantity="0.5")
    run(erp, "production.reverse", {"id": posted})
    assert deliver(erp, identifier)["status"] == "reversed"
    assert balance(erp, "physical.bundle") == (0, 0, 0)


def test_reverse_protects_finished_goods_already_reserved_by_sales(erp):
    identifier = create(erp)
    run(erp, "production.start", {"id": identifier})
    receipt = deliver(erp, identifier)["id"]
    order(erp, sku="physical.bundle")
    before = erp.snapshot()
    with pytest.raises(ERPError):
        run(erp, "production.reverse", {"id": receipt})
    assert erp.snapshot() == before
    assert state(erp)["receipts"][0]["status"] == "posted"


def test_assembly_transfers_exact_component_value_and_conversion_cost(erp):
    for sku, value in [("demo.aparelho", "12.50"), ("demo.fone", "2.002")]:
        run(
            erp,
            "valuation.set",
            {"sku": sku, "depot": "geral", "unit_cost": value, "reason": "Abertura fictícia"},
        )
    run(
        erp,
        "stock.post",
        {
            "sku": "demo.aparelho",
            "depot": "geral",
            "type": "E",
            "quantity": "1",
            "unit_cost": "0.01",
            "reason": "Entrada fictícia",
        },
    )
    identifier = create(erp)
    with erp.connection() as db:
        before = sum(
            procurement.pool(erp, db, s, "geral")
            for s in ("demo.aparelho", "demo.fone", "physical.bundle")
        )
    run(erp, "production.start", {"id": identifier})
    receipt = deliver(erp, identifier, extra_cost="1.01")["id"]
    with erp.connection() as db:
        after = sum(
            procurement.pool(erp, db, s, "geral")
            for s in ("demo.aparelho", "demo.fone", "physical.bundle")
        )
    assert after == before + Decimal("1.01")
    run(erp, "production.reverse", {"id": receipt})
    with erp.connection() as db:
        restored = sum(
            procurement.pool(erp, db, s, "geral")
            for s in ("demo.aparelho", "demo.fone", "physical.bundle")
        )
    assert restored == before
    assert not erp.snapshot()["accounts"]


def test_unknown_component_cost_is_never_replaced_by_finished_catalog_cost(erp):
    identifier = create(erp)
    run(erp, "production.start", {"id": identifier})
    deliver(erp, identifier)
    with erp.connection() as db:
        assert procurement.pool(erp, db, "physical.bundle", "geral") is None
    assert state(erp)["receipts"][0]["data"]["output_value"] is None


@pytest.mark.parametrize(
    "changes",
    [
        {"sku": "demo.kit"},
        {"components": [{"sku": "demo.kit", "quantity": "1"}]},
        {"components": [{"sku": "physical.bundle", "quantity": "1"}]},
        {"quantity": "0.001", "components": [{"sku": "demo.fone", "quantity": "0.001"}]},
        {"start_on": "2026-11-06", "finish_on": "2026-11-05"},
    ],
)
def test_invalid_physical_composition_and_dates(erp, changes):
    with pytest.raises(ERPError):
        create(erp, **changes)
