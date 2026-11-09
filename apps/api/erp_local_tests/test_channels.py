import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.erp_local.channels import ChannelFlow
from app.erp_local.core import ERPError, LocalERP
from app.erp_local.parity import parity_status


def event(identifier="evt1", kind="order.confirmed", *, minute=0, order="sale1", sku="demo.kit"):
    created = datetime.now(UTC) - timedelta(hours=1)
    return {
        "channel": "ml",
        "store": "store-uuid",
        "event_id": identifier,
        "external_id": order,
        "event_type": kind,
        "order_created_at": created.isoformat(),
        "occurred_at": (created + timedelta(minutes=minute)).isoformat(),
        "depot": "geral",
        "items": [{"sku": sku, "quantity": "1", "unit_price": "12.3456"}]
        if kind == "order.confirmed"
        else [],
    }


def routes(erp):
    with erp.connection() as db:
        db.execute(
            "INSERT INTO stock_routes VALUES "
            "('phone','demo.aparelho','ml','store-uuid','Loja real','MLB1',NULL,'geral',1,'x')"
        )
        db.execute(
            "INSERT INTO stock_routes VALUES "
            "('kit','demo.kit','ml','store-uuid','Loja real','MLB2','123','geral',1,'x')"
        )


def test_receipt_replay_survives_restart_and_conflicting_replay_is_rejected(erp):
    payload = event()
    first = ChannelFlow(erp).receive(payload)
    again = ChannelFlow(LocalERP(erp.database)).receive(payload)
    assert first["status"] == "processed" and again["replayed"]
    assert len(erp.snapshot()["orders"]) == 1
    payload["items"][0]["quantity"] = "2"
    with pytest.raises(ERPError, match="outros dados"):
        ChannelFlow(erp).receive(payload)


def test_stock_and_kit_previews_are_atomic_and_supersede_old_values(erp):
    routes(erp)
    flow = ChannelFlow(erp)
    flow.receive(event())
    preview = flow.snapshot()
    assert {r["sku"]: r["quantity"] for r in preview["intents"]} == {
        "demo.aparelho": "7",
        "demo.kit": "4",
    }
    flow.receive(event("cancel", "order.cancelled", minute=1))
    preview = flow.snapshot()
    assert preview["intent_counts"] == {"preview": 2, "superseded": 2}
    assert {r["sku"]: r["quantity"] for r in preview["intents"] if r["status"] == "preview"} == {
        "demo.aparelho": "8",
        "demo.kit": "5",
    }
    assert preview["external_writes"] is False and preview["transport_available"] is False


def test_insufficient_component_keeps_receipt_without_partial_reservation_or_intents(erp):
    routes(erp)
    payload = event()
    payload["items"][0]["quantity"] = "6"
    flow = ChannelFlow(erp)
    result = flow.receive(payload)
    assert result["status"] == "review" and result["outcome"]["code"] == "insufficient_stock"
    assert not erp.snapshot()["orders"] and not flow.snapshot()["intents"]
    assert all(
        b["reserved"] == "0"
        for p in erp.snapshot()["products"]
        if p["kind"] == "simple"
        for b in p["balances"]
    )
    assert not any(e["operation_key"].startswith("inbox-") for e in erp.snapshot()["events"])


def test_cancellation_before_confirmation_does_not_resurrect_reservation(erp):
    flow = ChannelFlow(erp)
    flow.receive(event("cancel", "order.cancelled", minute=2))
    assert flow.receive(event(minute=0))["status"] == "ignored"
    assert flow.receive(event("late-paid", minute=4))["outcome"]["code"] == "terminal_order"
    assert not erp.snapshot()["orders"]


def test_dispatch_before_confirmation_waits_and_recovers(erp):
    flow = ChannelFlow(erp)
    assert flow.receive(event("ship", "order.shipped", minute=3))["status"] == "deferred"
    flow.receive(event(minute=0))
    assert erp.snapshot()["orders"][0]["status"] == "shipped"
    assert flow.snapshot()["inbox_counts"] == {"processed": 2}
    assert len(erp.snapshot()["accounts"]) == 1


def test_late_cancellation_after_dispatch_requires_review(erp):
    flow = ChannelFlow(erp)
    flow.receive(event())
    flow.receive(event("ship", "order.shipped", minute=1))
    result = flow.receive(event("cancel", "order.cancelled", minute=2))
    assert result["status"] == "review" and result["outcome"]["code"] == "invalid_transition"
    assert erp.snapshot()["orders"][0]["status"] == "shipped"


def test_received_but_unprocessed_event_is_recovered_after_restart(erp, monkeypatch):
    flow = ChannelFlow(erp)

    def crash(_identifier):
        raise RuntimeError("crash after durable receipt")

    monkeypatch.setattr(flow, "process", crash)
    with pytest.raises(RuntimeError):
        flow.receive(event())
    restarted = ChannelFlow(LocalERP(erp.database))
    assert restarted.snapshot()["inbox_counts"] == {"received": 1}
    restarted.recover()
    assert restarted.snapshot()["inbox_counts"] == {"processed": 1}
    assert len(erp.snapshot()["orders"]) == 1


def test_parallel_repeated_event_has_one_receipt_and_effect(erp):
    payload = event()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: ChannelFlow(erp).receive(payload), range(4)))
    assert sum(not r["replayed"] for r in results) == 1
    assert len(erp.snapshot()["orders"]) == 1
    assert ChannelFlow(erp).snapshot()["inbox_counts"] == {"processed": 1}


def test_historic_order_is_not_reserved_on_top_of_opening_aggregate(erp):
    with erp.connection() as db:
        db.execute(
            "INSERT INTO catalog_import VALUES (1,?)",
            (json.dumps({"loaded_at": datetime.now(UTC).isoformat()}),),
        )
    result = ChannelFlow(erp).receive(event())
    assert result["outcome"]["code"] == "opening_reservation_review"
    assert not erp.snapshot()["orders"]


def test_extra_fields_and_unknown_event_status_are_rejected(erp):
    for extra in (
        {"access_token": "synthetic"},
        {"event_type": "partially_refunded"},
        {"occurred_at": "2026-11-05"},
    ):
        with pytest.raises(ERPError):
            ChannelFlow(erp).receive({**event(), **extra})
    assert not ChannelFlow(erp).snapshot()["inbox"]


def test_routes_are_prepared_only_for_active_matching_channel_and_own_identity(erp):
    with erp.connection() as db:
        db.execute(
            "INSERT INTO catalog_origin VALUES ('demo.aparelho',?)",
            (json.dumps({"source_id": "p1"}),),
        )
    links = [
        {
            "id": "r1",
            "product_id": "p1",
            "integration_id": "s1",
            "platform": "ml",
            "external_id": "MLB1",
            "variation_id": None,
        },
        {
            "id": "r2",
            "product_id": "missing",
            "integration_id": "s1",
            "platform": "ml",
            "external_id": "MLB2",
            "variation_id": None,
        },
    ]
    integration = {
        "id": "s1",
        "name": "Store",
        "platform": "ml",
        "status": "active",
        "archived_at": None,
    }
    reference = SimpleNamespace(
        info=lambda: {"snapshot": {"digest": "d"}},
        all_rows=lambda kind: links if kind == "links" else [integration],
    )
    flow = ChannelFlow(erp)
    assert flow.prepare_routes(reference)["routes"] == 1
    assert flow.prepare_routes(reference)["replayed"]
    assert flow.snapshot()["stores"] == 1
    assert not flow.snapshot()["intents"]  # no unsolicited publication of the opening catalog


def test_rea_contract_assets_are_pinned_and_do_not_claim_complete_server_equivalence():
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    data = parity_status()
    assert "não foram obtidos" in data["scope"]
    assert any(
        c["id"] == "fiscal" and c["status"] in {"mapped", "partial"} for c in data["contracts"]
    )
    for contract in data["contracts"]:
        for asset in contract["assets"]:
            assert (
                hashlib.sha256((root / asset["path"]).read_bytes()).hexdigest() == asset["sha256"]
            )
        for test in contract["tests"]:
            assert (root / test).is_file()


def test_ambiguous_account_listing_target_is_quarantined_instead_of_publishing_two_skus(erp):
    with erp.connection() as db:
        for sku, source in [("demo.aparelho", "p1"), ("demo.fone", "p2")]:
            db.execute(
                "INSERT INTO catalog_origin VALUES (?,?)", (sku, json.dumps({"source_id": source}))
            )
    links = [
        {
            "id": "r" + str(i),
            "product_id": "p" + str(i),
            "integration_id": "s1",
            "platform": "ml",
            "external_id": "MLB1",
            "variation_id": None,
        }
        for i in (1, 2)
    ]
    integration = {
        "id": "s1",
        "name": "Store",
        "platform": "ml",
        "status": "active",
        "archived_at": None,
    }
    reference = SimpleNamespace(
        info=lambda: {"snapshot": {"digest": "d"}},
        all_rows=lambda kind: links if kind == "links" else [integration],
    )
    result = ChannelFlow(erp).prepare_routes(reference)
    assert result["routes"] == 0 and result["ambiguous_targets"] == 1


def test_reference_refresh_preserves_current_preview_for_unchanged_target(erp):
    with erp.connection() as db:
        origin = {
            "source_id": "p1",
            "catalog_state": "ready",
            "stock_state": "known",
            "sellable": True,
        }
        db.execute("INSERT INTO catalog_origin VALUES ('demo.aparelho',?)", (json.dumps(origin),))
    link = {
        "id": "r1",
        "product_id": "p1",
        "integration_id": "s1",
        "platform": "ml",
        "external_id": "MLB1",
        "variation_id": None,
    }
    integration = {
        "id": "s1",
        "name": "Store",
        "platform": "ml",
        "status": "active",
        "archived_at": None,
    }
    reference = SimpleNamespace(
        info=lambda: {"snapshot": {"digest": "d1"}},
        all_rows=lambda kind: [link] if kind == "links" else [integration],
    )
    flow = ChannelFlow(erp)
    flow.prepare_routes(reference)
    erp.command(
        "increase",
        "stock.post",
        {"sku": "demo.aparelho", "depot": "geral", "type": "E", "quantity": "1", "reason": "test"},
    )
    assert flow.snapshot()["intent_counts"] == {"preview": 1}
    reference.info = lambda: {"snapshot": {"digest": "d2"}}
    flow.prepare_routes(reference)
    assert flow.snapshot()["intent_counts"] == {"preview": 1}
    link["external_id"] = "MLB2"
    reference.info = lambda: {"snapshot": {"digest": "d3"}}
    flow.prepare_routes(reference)
    assert flow.snapshot()["intent_counts"] == {"superseded": 1}
