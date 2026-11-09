from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from test_core import balance, order, run

from app.erp_local.core import ERPError, LocalERP
from app.erp_local.logistics import import_label, snapshot
from app.erp_local.release import backup, inspect_database, restore


def log(erp):
    with erp.connection() as db:
        return snapshot(db)


def prepared(erp, external="1", **kw):
    identifier = order(erp, external=external, **kw)["id"]
    run(erp, "logistics.prepare", {"id": identifier, "service": "Entrega local de teste"})
    return identifier


def package(erp, identifier):
    data = next(s for s in log(erp)["shipments"] if s["id"] == identifier)["data"]
    return [
        {
            "id": "V1",
            "weight_kg": "0.7",
            "height_cm": "10",
            "width_cm": "20",
            "length_cm": "30",
            "items": [{"sku": s, "quantity": q} for s, q in data["requirements"].items()],
        }
    ]


def packed(erp, identifier):
    for i, line in enumerate(package(erp, identifier)[0]["items"]):
        run(erp, "logistics.scan", {"id": identifier, "scan_id": f"scan-{i}", **line})
    run(erp, "logistics.pack", {"id": identifier, "packages": package(erp, identifier)})
    return identifier


def ship(erp, identifier):
    return run(erp, "logistics.dispatch", {"id": identifier, "proof": "Comprovante local"})


def test_kit_checkout_dispatch_and_receivable_are_separate_and_exactly_once(erp):
    identifier = prepared(erp)
    assert balance(erp) == (8, 1, 7)
    before = erp.snapshot()
    with pytest.raises(ERPError, match="Confira todas"):
        run(erp, "logistics.pack", {"id": identifier, "packages": package(erp, identifier)})
    assert erp.snapshot() == before
    packed(erp, identifier)
    assert balance(erp) == (8, 1, 7)
    assert erp.snapshot()["accounts"] == []
    with pytest.raises(ERPError) as e:
        run(erp, "order.ship", {"id": identifier})
    assert e.value.code == "logistics_required"
    result = run(erp, "logistics.dispatch", {"id": identifier, "proof": "P1"}, key="dispatch-1")
    assert result["external_writes"] is False
    assert run(erp, "logistics.dispatch", {"id": identifier, "proof": "P1"}, key="dispatch-1")[
        "replayed"
    ]
    with pytest.raises(ERPError):
        ship(erp, identifier)
    assert balance(erp) == (7, 0, 7)
    assert balance(erp, "demo.fone") == (4, 0, 4)
    assert len(erp.snapshot()["accounts"]) == 1


def test_scan_identifier_survives_retry_reset_and_concurrent_submission(erp):
    identifier = prepared(erp)
    payload = {"id": identifier, "scan_id": "barcode-1", "sku": "demo.aparelho", "quantity": "1"}
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run(erp, "logistics.scan", payload), range(2)))
    assert sorted(r["duplicate"] for r in results) == [False, True]
    with pytest.raises(ERPError):
        run(erp, "logistics.scan", {**payload, "quantity": "2"})
    with pytest.raises(ERPError):
        run(erp, "logistics.scan", {**payload, "scan_id": "overflow"})
    run(erp, "logistics.reset", {"id": identifier})
    assert log(erp)["shipments"][0]["scanned"] == {}
    assert run(erp, "logistics.scan", payload)["duplicate"] is True
    assert log(erp)["shipments"][0]["scanned"] == {}
    run(erp, "logistics.scan", {**payload, "scan_id": "new-cycle"})
    assert log(erp)["shipments"][0]["scanned"] == {"demo.aparelho": "1.000"}


def test_packages_support_split_components_but_require_exact_totals(erp):
    identifier = prepared(erp)
    packed(erp, identifier)
    run(erp, "logistics.unpack", {"id": identifier})
    volumes = package(erp, identifier)
    second = {**volumes[0], "id": "V2", "items": [volumes[0]["items"].pop()]}
    with pytest.raises(ERPError):
        run(erp, "logistics.pack", {"id": identifier, "packages": volumes})
    run(erp, "logistics.pack", {"id": identifier, "packages": [*volumes, second]})
    assert len(log(erp)["shipments"][0]["data"]["packages"]) == 2
    assert balance(erp) == (8, 1, 7)


def test_manifest_scope_and_failed_dispatch_roll_back_all_effects(erp):
    first, second = prepared(erp, "1"), prepared(erp, "2")
    foreign = prepared(erp, "3", store="outra-conta")
    for identifier in [first, second, foreign]:
        packed(erp, identifier)
    with pytest.raises(ERPError) as e:
        run(erp, "logistics.manifest.create", {"ids": [first, foreign]})
    assert e.value.code == "manifest_conflict"
    assert not log(erp)["shipments"][0]["data"].get("manifest_id")
    manifest = run(erp, "logistics.manifest.create", {"ids": [first, second]})["id"]
    with pytest.raises(ERPError):
        ship(erp, first)
    run(erp, "order.cancel", {"id": second})
    before = erp.snapshot()
    with pytest.raises(ERPError):
        run(erp, "logistics.manifest.dispatch", {"id": manifest, "proof": "P1"})
    assert erp.snapshot() == before
    assert erp.snapshot()["accounts"] == []
    run(erp, "logistics.manifest.cancel", {"id": manifest})
    ship(erp, first)
    assert balance(erp) == (7, 1, 6)  # Foreign order remains reserved.


def test_manifest_dispatch_moves_every_order_once(erp):
    selected = [packed(erp, prepared(erp, n)) for n in ["1", "2"]]
    manifest = run(erp, "logistics.manifest.create", {"ids": selected})["id"]
    run(erp, "logistics.manifest.dispatch", {"id": manifest, "proof": "P1"}, key="batch-dispatch")
    run(erp, "logistics.manifest.dispatch", {"id": manifest, "proof": "P1"}, key="batch-dispatch")
    assert balance(erp) == (6, 0, 6)
    assert len(erp.snapshot()["accounts"]) == 2
    assert log(erp)["manifests"][0]["status"] == "dispatched"


def test_batch_cancel_is_atomic_and_does_not_emit_external_work(erp):
    a, b = prepared(erp, "1"), prepared(erp, "2")
    run(erp, "order.cancel", {"id": b})
    before = erp.snapshot()
    with pytest.raises(ERPError):
        run(erp, "orders.batch", {"ids": [a, b], "action": "cancel"})
    assert erp.snapshot() == before
    run(erp, "orders.batch", {"ids": [a], "action": "cancel"})
    assert balance(erp) == (8, 0, 8)
    assert all(s["stage"] == "cancelled" for s in log(erp)["shipments"])


def test_tracking_reordering_and_carrier_return_never_restock(erp):
    identifier = packed(erp, prepared(erp))
    ship(erp, identifier)
    stamp = datetime.fromisoformat(log(erp)["shipments"][0]["data"]["dispatched_at"])

    def event(code, status, delta):
        return run(
            erp,
            "logistics.track",
            {
                "id": identifier,
                "event_id": code,
                "status": status,
                "occurred_at": (stamp + timedelta(seconds=delta)).isoformat(),
            },
        )

    assert event("before", "in_transit", -1)["disposition"] == "before_dispatch"
    assert event("delivered", "delivered", 2)["disposition"] == "applied"
    assert event("older", "in_transit", 1)["disposition"] == "older_or_simultaneous"
    assert event("bad-regression", "in_transit", 3)["disposition"] == "needs_review"
    assert event("delivered", "delivered", 2)["duplicate"]
    with pytest.raises(ERPError):
        event("delivered", "in_transit", 2)
    event("return", "return_in_transit", 4)
    event("warehouse", "returned_to_carrier", 5)
    event("seller", "returned_to_seller", 6)
    assert balance(erp) == (7, 0, 7)
    assert erp.snapshot()["orders"][0]["status"] == "shipped"
    run(erp, "logistics.return.open", {"id": identifier, "reason": "Teste de retorno"})
    run(
        erp,
        "logistics.return.receive",
        {"id": identifier, "receipt_id": "REC1", "destination": "retornos", "condition": "new"},
        key="receive-return",
    )
    run(
        erp,
        "logistics.return.receive",
        {"id": identifier, "receipt_id": "REC1", "destination": "retornos", "condition": "new"},
        key="receive-return",
    )
    assert balance(erp, depot="retornos") == (1, 0, 1)
    assert erp.snapshot()["accounts"][0]["status"] == "cancelled"
    assert log(erp)["shipments"][0]["stage"] == "returned"


def test_damaged_return_is_held_outside_available_stock_and_refunds(erp):
    identifier = packed(erp, prepared(erp))
    ship(erp, identifier)
    with pytest.raises(ERPError):
        run(erp, "order.return", {"id": identifier, "destination": "retornos"})
    run(erp, "logistics.return.open", {"id": identifier, "reason": "Avaria"})
    before = erp.snapshot()
    run(
        erp,
        "logistics.return.receive",
        {"id": identifier, "receipt_id": "REC", "destination": "retornos", "condition": "damaged"},
    )
    after = erp.snapshot()
    assert after["movements"] == before["movements"]
    assert after["accounts"] == before["accounts"]
    assert log(erp)["shipments"][0]["data"]["return"]["status"] == "quarantine_pending"
    with pytest.raises(ERPError):
        run(
            erp,
            "logistics.return.receive",
            {"id": identifier, "receipt_id": "REC2", "destination": "geral", "condition": "new"},
        )


def test_operator_documents_preserve_original_bytes_without_carrier_authority(erp):
    identifier = packed(erp, prepared(erp))
    raw = b"^XA\n^FO20,20^FDTESTE INTERNO SEM VALIDADE DE POSTAGEM^FS\n^XZ\n"
    result = import_label(erp, identifier, "zpl", raw)
    assert result["verified"] is False
    assert import_label(erp, identifier, "zpl", raw)["duplicate"]
    with erp.connection() as db:
        assert bytes(db.execute("SELECT content FROM logistics_labels").fetchone()[0]) == raw
    assert "content" not in log(erp)["labels"][0]
    run(erp, "logistics.label.prepare", {"id": identifier})
    assert log(erp)["shipments"][0]["data"]["label_request"]["status"] == "blocked"
    ship(erp, identifier)
    assert import_label(erp, identifier, "zpl", b"^XA^FDOUTRO TESTE^FS^XZ")["verified"] is False
    with pytest.raises(ERPError):
        import_label(erp, identifier, "pdf", b"not a PDF")


def test_backup_restore_rehearsal_preserves_labels_ledger_and_private_permissions(erp, tmp_path):
    identifier = packed(erp, prepared(erp))
    import_label(erp, identifier, "zpl", b"^XA^FDTESTE^FS^XZ")
    ship(erp, identifier)
    source_state = erp.snapshot()
    target = tmp_path / "backup.sqlite3"
    copy = backup(erp.database, target)
    restored = tmp_path / "restored.sqlite3"
    assert restore(target, restored)["restore_verified"]
    assert inspect_database(restored)["ledger_ok"]
    assert not inspect_database(restored)["production_ready"]
    assert LocalERP(restored).snapshot() == source_state
    assert erp.snapshot() == source_state
    assert Path(copy["manifest"]).stat().st_mode & 0o777 == 0o600
    assert target.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ERPError):
        restore(target, erp.database)
    assert erp.snapshot() == source_state
    with target.open("ab") as f:
        f.write(b"corruption")
    with pytest.raises(ERPError) as e:
        restore(target, tmp_path / "corrupted.sqlite3")
    assert e.value.code == "backup_checksum"


def test_preflight_detects_stock_corruption(erp):
    assert inspect_database(erp.database)["ledger_ok"]
    with erp.connection() as db:
        db.execute("UPDATE balances SET reserved='0.5' WHERE sku='demo.aparelho'")
    assert "stock_ledger_mismatch" in inspect_database(erp.database)["issues"]


def test_order_date_and_customer_are_preserved_and_conflicts_are_blocked(erp):
    p = {
        "channel": "local",
        "store": "demo",
        "external_id": "dated",
        "depot": "geral",
        "items": [{"sku": "demo.aparelho", "quantity": "1", "unit_price": "10"}],
        "customer_name": "Cliente fictício",
        "date": "2026-11-05",
        "expected_date": "2026-11-07",
    }
    run(erp, "order.import", p)
    assert run(erp, "order.import", p)["duplicate"]
    with pytest.raises(ERPError):
        run(erp, "order.import", {**p, "date": "2026-13-01", "external_id": "invalid-date"})
    with pytest.raises(ERPError) as e:
        run(erp, "order.import", {**p, "customer_name": "Outro cliente"})
    assert e.value.code == "order_conflict"
    assert len(erp.snapshot()["orders"]) == 1
