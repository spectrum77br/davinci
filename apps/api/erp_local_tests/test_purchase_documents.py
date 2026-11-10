import copy

import pytest

from app.erp_local import finance, procurement
from app.erp_local.core import ERPError


def snapshot(erp):
    with erp.connection() as db:
        return {
            **erp.snapshot(),
            "finance": finance.snapshot(db),
            "procurement": procurement.snapshot(erp, db),
        }


def purchase(erp, key="buy", **extra):
    payload = {
        "supplier": "Fornecedor local",
        "depot": "geral",
        "items": [{"sku": "demo.aparelho", "quantity": "2", "unit_price": "10"}],
        "date": "2026-11-05",
        "expected_date": "2026-11-06",
        "number": key,
        **extra,
    }
    return erp.command(key, "purchase.create", payload), payload


def test_purchase_edits_freeze_supplier_and_history_without_stock_or_accounts(erp):
    contact = erp.command(
        "supplier", "contact.save", {"data": {"name": "Fornecedor original", "roles": ["supplier"]}}
    )
    result, payload = purchase(erp, contact_id=contact["id"], notes="Pedido inicial")
    erp.command(
        "supplier-edit",
        "contact.save",
        {
            "id": contact["id"],
            "version": 1,
            "data": {"name": "Fornecedor atualizado", "roles": ["supplier"]},
        },
    )
    changed = erp.command(
        "edit",
        "purchase.update",
        {
            **payload,
            "id": result["id"],
            "version": 1,
            "reason": "Previsão alterada",
            "expected_date": "2026-11-10",
        },
    )
    assert changed["version"] == 2
    state = snapshot(erp)
    saved = next(p for p in state["purchases"] if p["id"] == result["id"])
    assert saved["data"]["supplier"] == "Fornecedor original"
    assert saved["data"]["contact"]["version"] == 1
    assert not state["accounts"]
    history = [h for h in state["procurement"]["history"] if h["purchase_id"] == result["id"]]
    assert [h["version"] for h in history] == [2, 1]
    refreshed = erp.command(
        "refresh-supplier",
        "purchase.update",
        {
            **payload,
            "id": result["id"],
            "version": 2,
            "reason": "Atualizar cadastro",
            "refresh_contact": True,
        },
    )
    assert refreshed["version"] == 3
    assert erp.snapshot()["purchases"][0]["data"]["supplier"] == "Fornecedor atualizado"


def test_purchase_paid_terms_are_frozen_and_split_exactly(erp):
    method = erp.command(
        "method",
        "payment_method.save",
        {"data": {"name": "Fornecedor 30/60", "applicability": "payable", "days": [30, 60]}},
    )
    result, payload = purchase(erp, payment_method_id=method["id"], freight="0.01")
    data = erp.snapshot()["purchases"][0]["data"]
    assert data["installments"] == [
        {"due_date": "2026-12-05", "amount": "10.01"},
        {"due_date": "2027-01-04", "amount": "10.00"},
    ]
    erp.command(
        "method-edit",
        "payment_method.save",
        {
            "id": method["id"],
            "version": 1,
            "data": {"name": "Fornecedor 30/60", "applicability": "payable", "days": [90]},
        },
    )
    erp.command(
        "edit",
        "purchase.update",
        {
            **payload,
            "id": result["id"],
            "version": 1,
            "reason": "Observação corrigida",
            "notes": "Novo texto",
        },
    )
    data2 = erp.snapshot()["purchases"][0]["data"]
    assert data2["payment_method"]["version"] == 1 and data2["installments"] == data["installments"]
    erp.command("post", "purchase.accounts", {"id": result["id"]})
    assert [i["due_date"] for i in snapshot(erp)["finance"]["installments"]] == [
        "2026-12-05",
        "2027-01-04",
    ]


def test_stale_edit_and_received_or_posted_purchase_are_atomic(erp):
    result, payload = purchase(erp)
    edit = {**payload, "id": result["id"], "version": 0, "reason": "Editar"}
    before = erp.snapshot()
    with pytest.raises(ERPError, match="A compra mudou"):
        erp.command("stale", "purchase.update", edit)
    assert erp.snapshot() == before
    erp.command(
        "receive",
        "purchase.receive",
        {"id": result["id"], "receipt_id": "REC-1", "lines": [{"line": 0, "quantity": "1"}]},
    )
    edit["version"] = 1
    for kind in ("purchase.update", "purchase.cancel"):
        before = erp.snapshot()
        with pytest.raises(ERPError, match="Estorne"):
            erp.command(
                kind,
                kind,
                edit
                if kind.endswith("update")
                else {k: edit[k] for k in ("id", "version", "reason")},
            )
        assert erp.snapshot() == before
    erp.command("reverse-stock", "purchase.reverse_stock", {"id": result["id"]})
    erp.command("post", "purchase.accounts", {"id": result["id"]})
    with pytest.raises(ERPError, match="Estorne"):
        erp.command("blocked", "purchase.update", edit)


def test_changed_purchase_reposts_new_total_and_preserves_cancelled_old_schedule(erp):
    result, payload = purchase(erp)
    erp.command("post", "purchase.accounts", {"id": result["id"]})
    erp.command("reverse", "purchase.reverse_accounts", {"id": result["id"]})
    erp.command(
        "edit",
        "purchase.update",
        {
            **payload,
            "id": result["id"],
            "version": 1,
            "reason": "Quantidade corrigida",
            "items": [{"sku": "demo.fone", "quantity": "3", "unit_price": "5"}],
        },
    )
    erp.command("repost", "purchase.accounts", {"id": result["id"]})
    state = snapshot(erp)
    assert state["accounts"][0]["amount"] == "15.00"
    assert len(state["finance"]["installments"]) == 1
    assert state["finance"]["installments"][0]["amount"] == "15.00"
    with erp.connection() as db:
        assert (
            db.execute("SELECT count(*) FROM finance_installments WHERE active=0").fetchone()[0]
            == 1
        )


def test_cancel_and_reopen_keep_versions_and_block_receiving(erp):
    result, payload = purchase(erp)
    erp.command(
        "cancel", "purchase.cancel", {"id": result["id"], "version": 1, "reason": "Compra suspensa"}
    )
    assert erp.snapshot()["purchases"][0]["status"] == "cancelled"
    for kind in ("purchase.stock", "purchase.accounts"):
        with pytest.raises(ERPError, match="Reabra"):
            erp.command(kind, kind, {"id": result["id"]})
    with pytest.raises(ERPError, match="Reabra"):
        erp.command(
            "edit",
            "purchase.update",
            {**payload, "id": result["id"], "version": 2, "reason": "Editar"},
        )
    erp.command(
        "reopen",
        "purchase.reopen",
        {"id": result["id"], "version": 2, "reason": "Fornecedor confirmou"},
    )
    assert erp.snapshot()["purchases"][0]["data"]["version"] == 3


def test_inspection_detects_changed_purchase_commercial_history(erp):
    from app.erp_local import release

    result, _ = purchase(erp)
    assert release.inspect_database(erp.database)["ledger_ok"]
    with erp.connection() as db:
        db.execute(
            "UPDATE purchase_document_history SET data=json_set(data,'$.supplier','Outra pessoa') "
            "WHERE purchase_id=?",
            (result["id"],),
        )
    assert "purchase_history_mismatch" in release.inspect_database(erp.database)["issues"]


def test_supplier_role_and_payment_applicability_cannot_be_assumed(erp):
    contact = erp.command(
        "contact", "contact.save", {"data": {"name": "Cliente", "roles": ["customer"]}}
    )
    method = erp.command(
        "method",
        "payment_method.save",
        {"data": {"name": "Recebimento", "applicability": "receivable"}},
    )
    for field, value in [("contact_id", contact["id"]), ("payment_method_id", method["id"])]:
        before = erp.snapshot()
        with pytest.raises(ERPError):
            purchase(erp, **{field: value})
        assert erp.snapshot() == before


def test_purchase_batch_failure_rolls_back_previous_receipt_and_account(erp):
    first, _ = purchase(erp, "FIRST")
    second, _ = purchase(erp, "SECOND")
    erp.command(
        "cancel-second", "purchase.cancel", {"id": second["id"], "version": 1, "reason": "Suspensa"}
    )
    for action in ["stock", "accounts"]:
        before = snapshot(erp)
        with pytest.raises(ERPError):
            erp.command(
                "batch-" + action,
                "purchases.batch",
                {"ids": [first["id"], second["id"]], "action": action},
            )
        assert snapshot(erp) == before


def test_purchase_batch_receives_only_remaining_and_posts_accounts_separately(erp):
    first, _ = purchase(erp, "FIRST")
    second, _ = purchase(erp, "SECOND")
    identifiers = [first["id"], second["id"]]
    erp.command(
        "part",
        "purchase.receive",
        {"id": first["id"], "receipt_id": "FIRST-REC", "lines": [{"line": 0, "quantity": "1"}]},
    )
    before = erp.snapshot()
    erp.command("batch", "purchases.batch", {"ids": identifiers, "action": "stock"})
    assert all(p["data"]["stock_posted"] for p in erp.snapshot()["purchases"])
    assert erp.snapshot()["accounts"] == before["accounts"]
    erp.command("accounts", "purchases.batch", {"ids": identifiers, "action": "accounts"})
    assert len(erp.snapshot()["accounts"]) == 2
    assert erp.command("accounts", "purchases.batch", {"ids": identifiers, "action": "accounts"})[
        "replayed"
    ]


@pytest.mark.parametrize(
    "extra",
    [
        {"expected_date": "2026-11-01"},
        {"packages": "1.5"},
        {"freight_payer": "x"},
        {"refresh_contact": "true"},
    ],
)
def test_invalid_purchase_metadata_does_not_create_a_document(erp, extra):
    before = copy.deepcopy(erp.snapshot())
    with pytest.raises(ERPError):
        purchase(erp, **extra)
    assert erp.snapshot() == before


def test_change_situation_in_batch_like_bling(erp):
    first, _ = purchase(erp, "SIT-1")
    second, _ = purchase(erp, "SIT-2")
    erp.command("attend", "purchases.batch", {"ids": [first["id"]], "action": "attend"})
    status = {p["id"]: (p["status"], p["data"]) for p in erp.snapshot()["purchases"]}
    assert status[first["id"]][0] == "received"
    assert status[first["id"]][1]["stock_posted"] and status[first["id"]][1]["accounts_posted"]
    erp.command("cancel", "purchases.batch", {"ids": [second["id"]], "action": "cancel"})
    assert {p["id"]: p["status"] for p in erp.snapshot()["purchases"]}[second["id"]] == "cancelled"
    # A cancelled purchase cannot become Atendido, and the whole selection rolls back.
    with pytest.raises(ERPError):
        erp.command("attend-2", "purchases.batch", {"ids": [second["id"]], "action": "attend"})
