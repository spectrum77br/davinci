import json
from decimal import Decimal

import pytest
from test_cash_register import current, register
from test_contacts import save as contact
from test_core import balance, run
from test_payment_methods import method
from test_sales import body, doc

from app.erp_local import finance
from app.erp_local.core import ERPError


def draft(erp, rid, **changes):
    order = body(**changes)
    for field in ["channel", "store", "status"]:
        order.pop(field, None)
    return run(
        erp,
        "pos.save",
        {"cash_id": rid, "cash_version": current(erp, rid)["data"]["version"], "order": order},
    )


def finalize_body(erp, rid, oid, tenders, **changes):
    return {
        "id": oid,
        "version": doc(erp, oid)["data"]["version"],
        "cash_version": current(erp, rid)["data"]["version"],
        "tenders": tenders,
        "cash_received": "0",
        **changes,
    }


def bank(erp, identifier="cash-bank", opening="1000"):
    run(erp, "bank.create", {"id": identifier, "name": identifier, "opening": opening})
    return identifier


def test_cash_checkout_posts_one_order_receipt_and_stock_net_of_change_atomically(erp):
    mid = method(erp, name="Dinheiro", type="cash", days=[0])["id"]
    bid = bank(erp)
    rid = register(erp)["id"]
    oid = draft(erp, rid)["id"]
    assert balance(erp) == (8, 0, 8) and not erp.snapshot()["accounts"]
    payload = finalize_body(
        erp,
        rid,
        oid,
        [{"method_id": mid, "amount": "12", "receipt": "received", "bank_id": bid}],
        cash_received="20",
    )
    result = erp.command("checkout-cash-once", "pos.finalize", payload)
    assert result["change"] == "8.00" and result["fiscal_issued"] is False
    assert result["external_payment_confirmation"] is False
    assert erp.command("checkout-cash-once", "pos.finalize", payload)["replayed"]
    assert balance(erp) == (7, 0, 7)
    with erp.connection() as db:
        f = finance.snapshot(db)
        assert f["banks"][0]["balance"] == "1012.00"
        assert len(f["payments"]) == 1
        assert (
            db.execute("SELECT status FROM accounts WHERE kind='receivable'").fetchone()[0]
            == "paid"
        )
    sale = doc(erp, oid)
    assert sale["status"] == "shipped" and sale["data"]["pos"]["cash_received"] == "20.00"
    assert current(erp, rid)["summary"]["cash_expected"] == "112.00"
    with pytest.raises(ERPError, match="finalizada"):
        run(
            erp,
            "pos.finalize",
            finalize_body(erp, rid, oid, payload["tenders"], cash_received="20"),
        )


def test_mixed_tenders_keep_bank_receipts_separate_from_forecast_fees(erp):
    cash = method(erp, name="Dinheiro", type="cash", days=[0])["id"]
    pix = method(erp, name="Pix manual", type="pix", days=[0])["id"]
    cb, pb = bank(erp), bank(erp, "pix-bank", "0")
    rid = register(erp)["id"]
    oid = draft(erp, rid)["id"]
    run(
        erp,
        "pos.finalize",
        finalize_body(
            erp,
            rid,
            oid,
            [
                {"method_id": cash, "amount": "7", "receipt": "received", "bank_id": cb},
                {
                    "method_id": pix,
                    "amount": "5",
                    "receipt": "received",
                    "bank_id": pb,
                    "reference": "PIX-LOCAL-1",
                },
            ],
            cash_received="10",
        ),
    )
    with erp.connection() as db:
        f = finance.snapshot(db)
        assert {b["id"]: b["balance"] for b in f["banks"]} == {cb: "1007.00", pb: "5.00"}
        assert all(json.loads(p["data"])["fee"] == "0.00" for p in f["payments"])
    sale = doc(erp, oid)["data"]
    assert sale["pos"]["change"] == "3.00"
    assert sum(Decimal(p["fee"]) for p in sale["payment_forecasts"]) > 0
    assert {g["method_id"]: g["expected"] for g in current(erp, rid)["summary"]["groups"]} == {
        "cash": "107.00",
        pix: "5.00",
    }


def test_checkout_failure_preserves_draft_and_has_no_receipts_accounts_or_reservations(erp):
    mid = method(erp, name="Dinheiro", type="cash", days=[0])["id"]
    bid = bank(erp)
    rid = register(erp)["id"]
    oid = draft(erp, rid, items=[{"sku": "demo.kit", "quantity": "100", "unit_price": "1"}])["id"]
    total = doc(erp, oid)["data"]["total"]
    before = balance(erp)
    with pytest.raises(ERPError):
        run(
            erp,
            "pos.finalize",
            finalize_body(
                erp,
                rid,
                oid,
                [{"method_id": mid, "amount": total, "receipt": "received", "bank_id": bid}],
                cash_received=total,
            ),
        )
    assert balance(erp) == before and not erp.snapshot()["accounts"]
    assert doc(erp, oid)["status"] == "draft"
    with erp.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM pos_receipt_proofs").fetchone()[0] == 0
        assert not finance.snapshot(db)["payments"]
    assert current(erp, rid)["summary"]["pending"]


def test_deferred_checkout_needs_customer_and_freezes_exact_installments(erp):
    mid = method(erp, name="Boleto", type="boleto", days=[0, 30, 60])["id"]
    rid = register(erp)["id"]
    oid = draft(erp, rid)["id"]
    with pytest.raises(ERPError, match="cadastro do cliente"):
        run(erp, "pos.finalize", finalize_body(erp, rid, oid, [{"method_id": mid, "amount": "12"}]))
    customer = contact(erp)["id"]
    draft(erp, rid, id=oid, version=1, contact_id=customer, customer_name="Cliente demonstrativo")
    run(erp, "pos.finalize", finalize_body(erp, rid, oid, [{"method_id": mid, "amount": "12"}]))
    data = doc(erp, oid)["data"]
    assert [p["amount"] for p in data["installments"]] == ["4.00"] * 3
    with erp.connection() as db:
        assert not finance.snapshot(db)["payments"]
        assert db.execute("SELECT status FROM accounts").fetchone()[0] == "open"
    assert current(erp, rid)["summary"]["cash_expected"] == "100.00"


def test_integrated_pix_is_not_faked_as_manually_confirmed(erp):
    mid = method(
        erp,
        name="Pix integrado",
        type="pix",
        integrated=True,
        institution_document="12345678000195",
        days=[0],
    )["id"]
    rid = register(erp)["id"]
    oid = draft(erp, rid)["id"]
    with pytest.raises(ERPError, match="conector próprio"):
        run(
            erp,
            "pos.finalize",
            finalize_body(
                erp,
                rid,
                oid,
                [
                    {
                        "method_id": mid,
                        "amount": "12",
                        "receipt": "received",
                        "reference": "not-proof",
                    }
                ],
            ),
        )
    assert doc(erp, oid)["status"] == "draft" and not erp.snapshot()["accounts"]


def test_received_bank_proof_cannot_be_used_on_another_pos_or_generic_finance_payment(erp):
    mid = method(erp, name="Pix manual", type="pix", days=[0])["id"]
    bid = bank(erp)
    rid = register(erp)["id"]
    first = draft(erp, rid)["id"]
    tender = [
        {
            "method_id": mid,
            "amount": "12",
            "receipt": "received",
            "bank_id": bid,
            "reference": "BANK-PROOF-1",
        }
    ]
    run(erp, "pos.finalize", finalize_body(erp, rid, first, tender))
    second = draft(erp, rid, external_id="MANUAL-002")["id"]
    with pytest.raises(ERPError, match="comprovante já foi registrado"):
        run(erp, "pos.finalize", finalize_body(erp, rid, second, tender))
    with erp.connection() as db:
        installment = finance.snapshot(db)["installments"][0]["id"]
    with pytest.raises(ERPError, match="comprovante já foi usado"):
        run(
            erp,
            "finance.pay",
            {
                "installment_id": installment,
                "amount": "1",
                "bank_id": bid,
                "external_id": "BANK-PROOF-1",
            },
        )
    assert doc(erp, second)["status"] == "draft"


def test_draft_cancel_is_one_transaction_and_ordinary_order_paths_cannot_bypass_pos(erp):
    rid = register(erp)["id"]
    oid = draft(erp, rid)["id"]
    for kind in ["order.confirm", "order.accounts", "order.stock", "order.ship", "order.cancel"]:
        with pytest.raises(ERPError, match="caixa"):
            run(erp, kind, {"id": oid})
    with pytest.raises(ERPError, match="caixa"):
        run(erp, "order.save", body(id=oid, version=1))
    run(
        erp,
        "pos.cancel",
        {"id": oid, "version": 1, "cash_version": 2, "reason": "Desistência fictícia"},
    )
    assert doc(erp, oid)["status"] == "cancelled"
    assert current(erp, rid)["summary"]["pending"] == []
    assert current(erp, rid)["summary"]["sales"][0]["status"] == "void"
    assert balance(erp) == (8, 0, 8)


def test_zero_total_sale_posts_stock_without_inventing_receipt_or_accounts(erp):
    rid = register(erp)["id"]
    oid = draft(erp, rid, discount="13")["id"]
    assert doc(erp, oid)["data"]["total"] == "0.00"
    run(erp, "pos.finalize", finalize_body(erp, rid, oid, []))
    assert doc(erp, oid)["status"] == "shipped"
    assert balance(erp) == (7, 0, 7) and not erp.snapshot()["accounts"]
    assert current(erp, rid)["summary"]["cash_expected"] == "100.00"


def test_wrong_cash_total_and_outdated_session_do_not_mutate_checkout(erp):
    mid = method(erp, name="Dinheiro", type="cash", days=[0])["id"]
    bid = bank(erp)
    rid = register(erp)["id"]
    oid = draft(erp, rid)["id"]
    tender = [{"method_id": mid, "amount": "12", "receipt": "received", "bank_id": bid}]
    with pytest.raises(ERPError, match="troco"):
        run(erp, "pos.finalize", finalize_body(erp, rid, oid, tender, cash_received="11"))
    with pytest.raises(ERPError, match="caixa mudou"):
        run(
            erp,
            "pos.finalize",
            finalize_body(erp, rid, oid, tender, cash_received="12", cash_version=1),
        )
    assert doc(erp, oid)["status"] == "draft" and not erp.snapshot()["accounts"]


def test_pos_receipt_is_non_fiscal_escaped_and_never_prints_internal_notes(erp):
    from app.erp_local.pos import print_html

    mid = method(erp, name="Dinheiro <img src=x>", type="cash", days=[0])["id"]
    bid = bank(erp)
    rid = register(erp)["id"]
    oid = draft(erp, rid, notes="<script>nota</script>", internal_notes="INTERNO-NAO-IMPRIMIR")[
        "id"
    ]
    with erp.connection() as db, pytest.raises(ERPError, match="Finalize"):
        print_html(erp, db, oid)
    run(
        erp,
        "pos.finalize",
        finalize_body(
            erp,
            rid,
            oid,
            [{"method_id": mid, "amount": "12", "receipt": "received", "bank_id": bid}],
            cash_received="20",
        ),
    )
    with erp.connection() as db:
        html = print_html(erp, db, oid)
    assert "Não é documento fiscal" in html and "Troco: R$ 8,00" in html
    assert "INTERNO-NAO-IMPRIMIR" not in html
    assert "&lt;script&gt;" in html and "<script>" not in html and "<img" not in html
