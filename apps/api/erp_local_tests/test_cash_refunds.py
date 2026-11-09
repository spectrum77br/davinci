from copy import deepcopy

import pytest
from test_cash_register import action, current, register
from test_core import run
from test_payment_methods import method
from test_pos import bank, draft, finalize_body

from app.erp_local import finance
from app.erp_local.core import ERPError
from app.erp_local.release import inspect_database


def sold(erp):
    bid = bank(erp)
    mid = method(erp, name="Dinheiro", type="cash", days=[0], bank_id=bid)["id"]
    rid = register(erp)["id"]
    oid = draft(erp, rid)["id"]
    run(
        erp,
        "pos.finalize",
        finalize_body(
            erp,
            rid,
            oid,
            [{"method_id": mid, "amount": "12", "receipt": "received", "bank_id": bid}],
            cash_received="12",
        ),
    )
    return bid, mid, rid, oid


def received(erp, oid, *, full=False):
    case = run(erp, "returns.open", {"id": oid, "reason": "Teste fictício"})["id"]
    lines = [{"sku": "demo.aparelho", "quantity": "1", "condition": "new", "destination": "geral"}]
    if full:
        lines.append(
            {"sku": "demo.fone", "quantity": "1", "condition": "new", "destination": "geral"}
        )
    run(erp, "returns.receive", {"id": case, "receipt_id": "FISICO-1", "lines": lines})
    if full:
        run(erp, "returns.close", {"id": case, "financial": "cancel_sale"})


def refund(erp, oid, bid, amount="5"):
    request = run(
        erp,
        "returns.refund",
        {"id": oid, "reference": "DEV-1", "amount": amount, "reason": "Reembolso aprovado"},
    )["id"]
    with erp.connection() as db:
        account = db.execute(
            "SELECT id FROM accounts WHERE document_id=? AND kind='refund'", (request,)
        ).fetchone()[0]
        installment = next(
            i for i in finance.snapshot(db)["installments"] if i["account_id"] == account
        )
    return run(
        erp,
        "finance.pay",
        {
            "installment_id": installment["id"],
            "amount": amount,
            "bank_id": bid,
            "external_id": "PAG-DEV-1",
        },
    )["id"]


def test_partial_refund_classifies_one_existing_cash_outflow_without_paying_it_twice(erp):
    bid, mid, rid, oid = sold(erp)
    received(erp, oid)
    pid = refund(erp, oid, bid)
    with erp.connection() as db:
        before = finance.snapshot(db)
    assert current(erp, rid)["summary"]["cash_expected"] == "112.00"
    with pytest.raises(ERPError, match="concilie"):
        action(erp, rid, "cash.close", counted={"cash": "107"}, reason="")
    classified = action(
        erp, rid, "cash.refund", payment_id=pid, method_id=mid, reason="Dinheiro devolvido"
    )
    detail = current(erp, rid)
    assert detail["summary"]["cash_expected"] == "107.00"
    assert detail["summary"]["groups"][0]["sales"] == "12.00"
    assert detail["summary"]["groups"][0]["refunds"] == "5.00"
    assert not detail["summary"]["pending"]
    with erp.connection() as db:
        assert finance.snapshot(db) == before
    with pytest.raises(ERPError, match="já foi classificada"):
        action(erp, rid, "cash.refund", payment_id=pid, method_id=mid, reason="Repetição")
    with pytest.raises(ERPError, match="classificação"):
        run(erp, "finance.reverse", {"id": pid})
    action(erp, rid, "cash.close", counted={"cash": "107"}, reason="")
    action(erp, rid, "cash.verify", reason="Conferido")
    assert inspect_database(erp.database)["ledger_ok"]
    action(erp, rid, "cash.unverify", reason="Corrigir classificação")
    action(erp, rid, "cash.reopen", reason="Corrigir classificação")
    action(
        erp,
        rid,
        "cash.refund.reverse",
        refund_id=classified["refund_id"],
        reason="Classificação incorreta",
    )
    run(erp, "finance.reverse", {"id": pid})
    assert current(erp, rid)["summary"]["cash_expected"] == "112.00"
    assert len(current(erp, rid)["data"]["verifications"]) == 1
    assert current(erp, rid)["data"]["closures"][0]["expected"] == {"cash": "107.00"}


def test_fully_paid_return_preserves_gross_sale_and_requires_separate_refund(erp):
    bid, mid, rid, oid = sold(erp)
    received(erp, oid, full=True)
    with erp.connection() as db:
        account = db.execute("SELECT id FROM accounts WHERE kind='refund'").fetchone()[0]
        installment = next(
            i for i in finance.snapshot(db)["installments"] if i["account_id"] == account
        )
    pid = run(
        erp,
        "finance.pay",
        {
            "installment_id": installment["id"],
            "amount": "12",
            "bank_id": bid,
            "external_id": "FULL-REFUND",
        },
    )["id"]
    with pytest.raises(ERPError, match="Preserve a venda"):
        action(erp, rid, "cash.void", order_id=oid, reason="Não apagar o recebimento")
    action(erp, rid, "cash.refund", payment_id=pid, method_id=mid, reason="Devolução integral")
    assert current(erp, rid)["summary"]["cash_expected"] == "100.00"
    action(erp, rid, "cash.close", counted={"cash": "100"}, reason="")
    action(erp, rid, "cash.verify", reason="Conferido")
    assert inspect_database(erp.database)["ledger_ok"]


def test_later_refund_belongs_to_new_period_and_never_rewrites_verified_sales(erp):
    bid, mid, rid, oid = sold(erp)
    action(erp, rid, "cash.close", counted={"cash": "112"}, reason="")
    action(erp, rid, "cash.verify", reason="Conferido antes da devolução")
    old = deepcopy(current(erp, rid)["data"])
    later = register(erp)["id"]
    received(erp, oid)
    pid = refund(erp, oid, bid)
    action(
        erp, later, "cash.refund", payment_id=pid, method_id=mid, reason="Saída no período atual"
    )
    assert current(erp, later)["summary"]["cash_expected"] == "95.00"
    assert current(erp, rid)["data"] == old
    assert current(erp, rid)["summary"]["cash_expected"] == "112.00"
    assert not current(erp, rid)["summary"]["pending"]
    assert inspect_database(erp.database)["ledger_ok"]
    action(erp, later, "cash.close", counted={"cash": "95"}, reason="")
    action(erp, later, "cash.verify", reason="Conferência da devolução posterior")
    action(erp, rid, "cash.unverify", reason="Tentativa de correção")
    with pytest.raises(ERPError, match="outro período"):
        action(erp, rid, "cash.reopen", reason="Evitar contagem dupla")


def test_refund_classification_requires_correct_bank_period_and_available_money(erp):
    bid, mid, rid, oid = sold(erp)
    received(erp, oid)
    wrong = method(erp, name="Dinheiro sem conta", type="cash", days=[0])["id"]
    pid = refund(erp, oid, bid)
    with pytest.raises(ERPError, match="conta da baixa"):
        action(erp, rid, "cash.refund", payment_id=pid, method_id=wrong, reason="Teste")
    later = register(erp, register="Outro balcão", opening="0")["id"]
    with pytest.raises(ERPError, match="anterior"):
        action(erp, later, "cash.refund", payment_id=pid, method_id=mid, reason="Teste")
    with pytest.raises(ERPError, match="baixa de reembolso"):
        action(erp, rid, "cash.refund", payment_id="inexistente", method_id=mid, reason="Teste")
    action(erp, rid, "cash.withdraw", amount="110", reference="SANGRIA-1", reason="Teste")
    with pytest.raises(ERPError, match="ultrapassa"):
        action(erp, rid, "cash.refund", payment_id=pid, method_id=mid, reason="Teste")
    assert current(erp, rid)["summary"]["refunds"] == []


def test_full_return_after_partial_refund_schedules_only_remaining_principal(erp):
    bid, mid, rid, oid = sold(erp)
    received(erp, oid)
    pid = refund(erp, oid, bid)
    action(erp, rid, "cash.refund", payment_id=pid, method_id=mid, reason="Parcial conferido")
    with erp.connection() as db:
        case = db.execute("SELECT id FROM return_cases WHERE order_id=?", (oid,)).fetchone()[0]
    run(
        erp,
        "returns.receive",
        {
            "id": case,
            "receipt_id": "FISICO-SALDO",
            "lines": [
                {"sku": "demo.fone", "quantity": "1", "condition": "new", "destination": "geral"}
            ],
        },
    )
    run(erp, "returns.close", {"id": case, "financial": "cancel_sale"})
    with erp.connection() as db:
        header = db.execute(
            "SELECT id,amount FROM accounts WHERE kind='refund' AND document_id=?", (oid,)
        ).fetchone()
        assert header["amount"] == "7.00"
        plan = [i for i in finance.snapshot(db)["installments"] if i["account_id"] == header["id"]]
        assert [i["amount"] for i in plan] == ["7.00"]
        assert len([p for p in finance.snapshot(db)["payments"] if not p["reversed"]]) == 2
    assert current(erp, rid)["summary"]["cash_expected"] == "107.00"
