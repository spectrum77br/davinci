import json

import pytest
from test_core import balance, run
from test_payment_methods import method
from test_sales import body, doc

from app.erp_local import proposals
from app.erp_local.core import ERPError


def quote(**changes):
    return {
        "number": "LOCAL-PROP-001",
        "channel": "local",
        "store": "manual",
        "date": "2026-11-05",
        "customer_name": "Cliente fictício",
        "items": [{"sku": "demo.kit", "quantity": "1", "unit_price": "10.0050"}],
        "freight": "3",
        "discount": "1",
        "charges": "2",
        "packages": 1,
        "internal_notes": "INTERNO-NAO-IMPRIMIR",
        **changes,
    }


def saved(erp, identifier):
    with erp.connection() as db:
        return next(q for q in proposals.snapshot(db) if q["id"] == identifier)


def test_quoting_stock_not_available_has_no_stock_order_or_cash_effect(erp):
    before = balance(erp)
    result = run(
        erp,
        "proposal.save",
        quote(items=[{"sku": "demo.kit", "quantity": "100", "unit_price": "10.0050"}]),
    )
    current = saved(erp, result["id"])
    assert current["data"]["total"] == "1004.50"
    assert balance(erp) == before
    assert not erp.snapshot()["orders"] and not erp.snapshot()["accounts"]
    assert len(current["history"]) == 1


def test_revision_saves_unsaved_form_values_and_preserves_previous_snapshot(erp):
    result = run(erp, "proposal.save", quote())
    run(
        erp,
        "proposal.revise",
        quote(id=result["id"], version=1, introduction="Nova condição", freight="5"),
    )
    current = saved(erp, result["id"])
    assert current["data"]["revision"] == 2
    assert current["data"]["total"] == "16.01"
    assert current["history"][-1]["data"]["total"] == "14.01"
    assert current["history"][-1]["data"]["introduction"] == ""
    with pytest.raises(ERPError, match="mudou"):
        run(erp, "proposal.save", quote(id=result["id"], version=1))
    assert saved(erp, result["id"])["data"]["introduction"] == "Nova condição"


def test_proposal_sale_link_is_atomic_idempotent_and_locks_origin(erp):
    prop = run(erp, "proposal.save", quote())["id"]
    payload = body(proposal_id=prop, proposal_version=1)
    before = balance(erp)
    result = erp.command("save-from-proposal", "order.save", payload)
    repeated = erp.command("save-from-proposal", "order.save", payload)
    assert repeated["replayed"] and result["id"] == repeated["id"]
    assert len(erp.snapshot()["orders"]) == 1 and balance(erp) == before
    assert saved(erp, prop)["data"]["linked_sale"] == result["id"]
    assert saved(erp, prop)["status"] == "converted"
    with erp.connection() as db:
        html = proposals.print_html(db, prop)
        assert "Venda gerada" in html and "converted" not in html
    assert doc(erp, result["id"])["data"]["proposal"]["total"] == "14.01"
    with pytest.raises(ERPError):
        run(erp, "order.save", body(external_id="SECOND", proposal_id=prop, proposal_version=2))
    with pytest.raises(ERPError, match="protegida"):
        run(erp, "proposal.revise", {"id": prop, "version": 2})
    run(erp, "order.cancel", {"id": result["id"]})
    assert saved(erp, prop)["data"]["linked_sale"] == result["id"]


def test_failed_sale_does_not_lock_proposal_or_leave_partial_reservation(erp):
    prop = run(erp, "proposal.save", quote())["id"]
    before = balance(erp)
    with pytest.raises(ERPError):
        run(
            erp,
            "order.save",
            body(
                status="reserved",
                proposal_id=prop,
                proposal_version=1,
                items=[{"sku": "demo.kit", "quantity": "100", "unit_price": "10"}],
            ),
        )
    assert saved(erp, prop)["status"] == "open"
    assert saved(erp, prop)["data"]["version"] == 1
    assert not erp.snapshot()["orders"] and balance(erp) == before


def test_proposal_identity_is_per_store_and_stale_or_expired_conversion_is_held(erp):
    first = run(erp, "proposal.save", quote(valid_until="2026-11-05"))["id"]
    with pytest.raises(ERPError, match="Já existe"):
        run(erp, "proposal.save", quote())
    second = run(erp, "proposal.save", quote(store="outra"))["id"]
    assert first != second
    with pytest.raises(ERPError, match="validade"):
        run(erp, "order.save", body(proposal_id=first, proposal_version=1, date="2026-11-06"))
    run(erp, "proposal.revise", {"id": first, "version": 1})
    with pytest.raises(ERPError, match="mudou"):
        run(erp, "order.save", body(proposal_id=first, proposal_version=1))


def test_batch_is_atomic_when_one_proposal_is_already_linked(erp):
    first = run(erp, "proposal.save", quote())["id"]
    second = run(erp, "proposal.save", quote(number="P2"))["id"]
    run(erp, "order.save", body(proposal_id=second, proposal_version=1))
    with pytest.raises(ERPError, match="protegida"):
        run(
            erp,
            "proposal.batch",
            {
                "status": "cancelled",
                "entries": [{"id": first, "version": 1}, {"id": second, "version": 2}],
            },
        )
    assert saved(erp, first)["status"] == "open"
    assert saved(erp, first)["data"]["version"] == 1


def test_free_items_are_kept_in_proposal_but_never_silently_dropped_from_sale(erp):
    prop = run(
        erp,
        "proposal.save",
        quote(
            items=[],
            other_total="20",
            other_description="Serviço eventual <img src=x>",
            discount="0",
        ),
    )["id"]
    current = saved(erp, prop)
    assert current["data"]["total"] == "25.00"
    with erp.connection() as db:
        html = proposals.print_html(db, prop)
    assert "INTERNO-NAO-IMPRIMIR" not in html
    assert "&lt;img src=x&gt;" in html and "<img" not in html
    with pytest.raises(ERPError, match="serviços"):
        run(erp, "order.save", body(proposal_id=prop, proposal_version=1))
    assert saved(erp, prop)["status"] == "open" and not erp.snapshot()["orders"]


def test_payment_conditions_are_frozen_without_receivable_or_commission_account(erp):
    mid = method(erp)["id"]
    prop = run(erp, "proposal.save", quote(payment_method_id=mid))["id"]
    current = saved(erp, prop)["data"]
    assert [i["amount"] for i in current["installments"]] == ["4.67", "4.67", "4.67"]
    with erp.connection() as db:
        assert "05/12/2026" in proposals.print_html(db, prop)
        assert db.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0
    run(
        erp,
        "payment_method.save",
        {"id": mid, "version": 1, "data": {"name": "Alterada", "type": "boleto", "days": [0]}},
    )
    run(erp, "proposal.save", quote(id=prop, version=1, payment_method_id=mid))
    assert saved(erp, prop)["data"]["installments"] == current["installments"]


def test_rejected_proposal_needs_new_revision_before_edit(erp):
    prop = run(erp, "proposal.save", quote())["id"]
    run(erp, "proposal.situation", {"id": prop, "version": 1, "status": "rejected"})
    with pytest.raises(ERPError, match="nova versão"):
        run(erp, "proposal.save", quote(id=prop, version=2))
    run(erp, "proposal.revise", quote(id=prop, version=2, greeting="Até breve"))
    current = saved(erp, prop)
    assert current["status"] == "open" and current["data"]["revision"] == 2
    assert current["data"]["greeting"] == "Até breve"


def test_unknown_fields_do_not_make_partial_versions(erp):
    prop = run(erp, "proposal.save", quote())["id"]
    with pytest.raises(ERPError, match="não reconhecidos"):
        run(erp, "proposal.revise", quote(id=prop, version=1, send_email=True))
    assert len(saved(erp, prop)["history"]) == 1
    with erp.connection() as db:
        assert "send_email" not in json.dumps(proposals.snapshot(db))


def test_proposal_http_print_export_and_state_are_secret_free(tmp_path):
    from uuid import uuid4

    from test_api import client

    with client(tmp_path) as c:

        def send(kind, payload):
            return c.post(
                "/api/erp-local/commands",
                headers={"X-ERP-Local": "1"},
                json={"operation_key": str(uuid4()), "kind": kind, "payload": payload},
            )

        assert send("demo.seed", {}).status_code == 200
        result = send("proposal.save", quote(number="=1+1", customer_name="<script>local</script>"))
        assert result.status_code == 200
        pid = result.json()["id"]
        html = c.get(f"/api/erp-local/proposals/{pid}/print")
        assert html.status_code == 200
        assert "&lt;script&gt;" in html.text and "<script>" not in html.text
        assert "INTERNO-NAO-IMPRIMIR" not in html.text
        csv = c.get("/api/erp-local/proposals.csv")
        assert csv.status_code == 200 and "'=1+1" in csv.text
        state = c.get("/api/erp-local/state").json()
        assert len(state["proposals"]) == 1 and not state["orders"] and not state["accounts"]


def test_release_detects_proposal_link_and_history_divergence(erp):
    from app.erp_local.release import inspect_database

    pid = run(erp, "proposal.save", quote())["id"]
    run(erp, "order.save", body(proposal_id=pid, proposal_version=1))
    assert inspect_database(erp.database)["ledger_ok"]
    with erp.connection() as db:
        db.execute("UPDATE commercial_proposals SET status='open' WHERE id=?", (pid,))
    issues = inspect_database(erp.database)["issues"]
    assert "proposal_sale_mismatch" in issues and "proposal_history_mismatch" in issues
