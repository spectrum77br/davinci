import pytest
from test_core import run
from test_payment_methods import method
from test_sales import body, doc

from app.erp_local import cash_register, finance
from app.erp_local.core import ERPError


def register(erp, **changes):
    return run(
        erp,
        "cash.open",
        {
            "register": "Balcão 1",
            "channel": "local",
            "store": "manual",
            "opening": "100",
            **changes,
        },
    )


def current(erp, identifier):
    with erp.connection() as db:
        return next(c for c in cash_register.snapshot(erp, db) if c["id"] == identifier)


def action(erp, register_id, kind, **values):
    return run(
        erp,
        kind,
        {"id": register_id, "version": current(erp, register_id)["data"]["version"], **values},
    )


def test_physical_open_supply_withdraw_close_never_create_revenue_or_payments(erp):
    with erp.connection() as db:
        before = finance.snapshot(db)
    opened = register(erp)
    rid = opened["id"]
    action(erp, rid, "cash.supply", reference="S1", amount="50", reason="Fundo de troco")
    action(erp, rid, "cash.withdraw", reference="W1", amount="20", reason="Retirada conferida")
    assert current(erp, rid)["summary"]["cash_expected"] == "130.00"
    action(erp, rid, "cash.close", counted={"cash": "130"}, reason="")
    assert current(erp, rid)["status"] == "closed"
    with erp.connection() as db:
        assert finance.snapshot(db) == before
    assert not erp.snapshot()["accounts"]


def test_one_open_session_per_register_store_and_stale_commands_cannot_change_it(erp):
    opened = register(erp)
    with pytest.raises(ERPError, match="já está aberto"):
        register(erp, register="BALCÃO 1")
    assert register(erp, register="Balcão 2")["id"] != opened["id"]
    with pytest.raises(ERPError, match="mudou"):
        run(
            erp,
            "cash.supply",
            {"id": opened["id"], "version": 0, "amount": "5", "reference": "S1", "reason": "Teste"},
        )
    assert current(erp, opened["id"])["operations"] == []


def test_duplicate_proofs_and_insufficient_cash_do_not_duplicate_or_erase_operations(erp):
    rid = register(erp)["id"]
    supplied = action(erp, rid, "cash.supply", amount="50", reference="S1", reason="Troco")
    with pytest.raises(ERPError, match="já foi registrado"):
        action(erp, rid, "cash.supply", amount="50", reference="S1", reason="Troco")
    action(erp, rid, "cash.withdraw", amount="130", reference="W1", reason="Recolhimento")
    with pytest.raises(ERPError, match="ultrapassa"):
        action(erp, rid, "cash.withdraw", amount="21", reference="W2", reason="Recolhimento")
    with pytest.raises(ERPError, match="já foi utilizado"):
        action(erp, rid, "cash.reverse", operation_id=supplied["operation_id"], reason="Estorno")
    assert len(current(erp, rid)["operations"]) == 2
    assert current(erp, rid)["summary"]["cash_expected"] == "20.00"


def test_closing_requires_all_counts_and_difference_reason_then_preserves_reopen_history(erp):
    rid = register(erp)["id"]
    with pytest.raises(ERPError, match="todas"):
        action(erp, rid, "cash.close", counted={}, reason="")
    with pytest.raises(ERPError, match="motivo da diferença"):
        action(erp, rid, "cash.close", counted={"cash": "99.50"}, reason="")
    action(erp, rid, "cash.close", counted={"cash": "99.50"}, reason="Falta de cinquenta centavos")
    assert current(erp, rid)["data"]["closure"]["differences"] == {"cash": "-0.50"}
    action(erp, rid, "cash.reopen", reason="Recontagem")
    assert current(erp, rid)["status"] == "open"
    assert len(current(erp, rid)["data"]["closures"]) == 1
    action(erp, rid, "cash.close", counted={"cash": "100"}, reason="")
    action(erp, rid, "cash.verify", reason="Conferência do responsável")
    assert current(erp, rid)["status"] == "verified"
    with pytest.raises(ERPError, match="não conferido"):
        action(erp, rid, "cash.reopen", reason="Teste")


def test_reopening_cannot_override_another_opening_for_same_register(erp):
    rid = register(erp)["id"]
    action(erp, rid, "cash.close", counted={"cash": "100"}, reason="")
    another = register(erp)["id"]
    with pytest.raises(ERPError, match="outra abertura"):
        action(erp, rid, "cash.reopen", reason="Teste")
    assert current(erp, another)["status"] == "open"


def test_linked_sales_require_full_tender_amounts_and_completed_orders_for_closure(erp):
    cash = method(erp, name="Dinheiro", type="cash", days=[0])["id"]
    pix = method(erp, name="Pix", type="pix", days=[0])["id"]
    rid = register(erp)["id"]
    order = run(erp, "order.save", body())["id"]
    tenders = [{"method_id": cash, "amount": "7"}, {"method_id": pix, "amount": "5"}]
    with pytest.raises(ERPError, match="fechar o total"):
        action(erp, rid, "cash.attach", order_id=order, order_version=1, tenders=tenders[:1])
    action(erp, rid, "cash.attach", order_id=order, order_version=1, tenders=tenders)
    assert current(erp, rid)["summary"]["pending"][0]["reason"] == "sale_unfinished"
    with pytest.raises(ERPError, match="Finalize"):
        action(erp, rid, "cash.close", counted={"cash": "100"}, reason="")
    run(erp, "order.confirm", {"id": order})
    run(erp, "order.stock", {"id": order})
    action(
        erp,
        rid,
        "cash.attach",
        order_id=order,
        order_version=doc(erp, order)["data"]["version"],
        tenders=tenders,
    )
    groups = current(erp, rid)["summary"]["groups"]
    assert {g["method_id"]: g["expected"] for g in groups} == {"cash": "107.00", pix: "5.00"}
    assert not erp.snapshot()["accounts"]
    action(erp, rid, "cash.close", counted={"cash": "107", pix: "5"}, reason="")
    assert current(erp, rid)["data"]["closure"]["differences"] == {"cash": "0.00", pix: "0.00"}


def test_sales_cannot_be_counted_in_two_registers_or_a_foreign_store(erp):
    mid = method(erp, name="Dinheiro", type="cash", days=[0])["id"]
    first, second = register(erp)["id"], register(erp, register="Balcão 2")["id"]
    order = run(erp, "order.save", body())["id"]
    tender = [{"method_id": mid, "amount": "12"}]
    action(erp, first, "cash.attach", order_id=order, order_version=1, tenders=tender)
    with pytest.raises(ERPError, match="outro caixa"):
        action(erp, second, "cash.attach", order_id=order, order_version=1, tenders=tender)
    foreign = run(erp, "order.save", body(store="outra-loja"))["id"]
    with pytest.raises(ERPError, match="outra loja"):
        action(erp, first, "cash.attach", order_id=foreign, order_version=1, tenders=tender)


def test_cancelled_sale_requires_explicit_reconciliation_and_closed_report_stays_frozen(erp):
    mid = method(erp, name="Dinheiro", type="cash", days=[0])["id"]
    rid = register(erp)["id"]
    order = run(erp, "order.save", body(status="reserved"))["id"]
    run(erp, "order.stock", {"id": order})
    action(
        erp,
        rid,
        "cash.attach",
        order_id=order,
        order_version=doc(erp, order)["data"]["version"],
        tenders=[{"method_id": mid, "amount": "12"}],
    )
    action(erp, rid, "cash.close", counted={"cash": "112"}, reason="")
    run(erp, "order.return", {"id": order, "destination": "geral"})
    assert current(erp, rid)["data"]["closure"]["expected"] == {"cash": "112.00"}
    from app.erp_local.release import inspect_database

    assert "cash_closed_sales_changed" in inspect_database(erp.database)["issues"]
    with pytest.raises(ERPError, match="mudaram após"):
        action(erp, rid, "cash.verify", reason="Conferência")
    action(erp, rid, "cash.reopen", reason="Conciliar devolução")
    action(erp, rid, "cash.void", order_id=order, reason="Devolução física e valores conferidos")
    assert current(erp, rid)["summary"]["cash_expected"] == "100.00"
    action(erp, rid, "cash.close", counted={"cash": "100"}, reason="")
    assert current(erp, rid)["data"]["closures"][0]["expected"] == {"cash": "112.00"}


def test_bank_movement_or_unknown_fields_never_sneak_into_physical_cash_operation(erp):
    rid = register(erp)["id"]
    with pytest.raises(ERPError, match="não reconhecidos"):
        action(
            erp,
            rid,
            "cash.supply",
            amount="5",
            reference="S1",
            reason="Teste",
            bank_transfer={"destination": "bank"},
        )
    assert current(erp, rid)["operations"] == []


def test_cash_print_http_escapes_notes_and_keeps_finance_unchanged(tmp_path):
    from uuid import uuid4

    from test_api import client

    with client(tmp_path) as c:

        def send(kind, payload):
            return c.post(
                "/api/erp-local/commands",
                headers={"X-ERP-Local": "1"},
                json={"operation_key": str(uuid4()), "kind": kind, "payload": payload},
            )

        send("demo.seed", {})
        result = send(
            "cash.open",
            {
                "register": "Balcão <img src=x>",
                "channel": "local",
                "store": "manual",
                "opening": "100",
            },
        )
        assert result.status_code == 200
        rid = result.json()["id"]
        result = send(
            "cash.supply",
            {
                "id": rid,
                "version": 1,
                "amount": "5",
                "reference": "S1",
                "reason": "<script>teste</script>",
            },
        )
        assert result.status_code == 200
        html = c.get(f"/api/erp-local/cash-register/{rid}/print")
        assert html.status_code == 200
        assert "&lt;img" in html.text and "&lt;script&gt;" in html.text
        assert "<img" not in html.text and "<script>" not in html.text
        state = c.get("/api/erp-local/state").json()
        assert state["cash_register"][0]["summary"]["cash_expected"] == "105.00"
        assert not state["accounts"] and not state["finance"]["payments"]
