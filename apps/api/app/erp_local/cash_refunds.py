"""Classify an already-recorded refund outflow in the physical cash period.

This does not pay or undo a refund. A later return must not rewrite a verified
earlier till's sales; its cash outflow belongs to the currently open period.
"""

import json
from decimal import Decimal
from uuid import uuid4

from app.erp_local.core import ERPError, dumps, number, text

SCHEMA = """
CREATE TABLE IF NOT EXISTS cash_refunds (
 id TEXT PRIMARY KEY, session_id TEXT REFERENCES cash_sessions(id),
 payment_id TEXT REFERENCES finance_payments(id), order_id TEXT REFERENCES documents(id),
 amount TEXT NOT NULL, reversed INTEGER NOT NULL DEFAULT 0, data TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS cash_refund_once ON cash_refunds(payment_id) WHERE reversed=0;
"""
COMMANDS = {"cash.refund", "cash.refund.reverse"}


def payment(db, identifier):
    row = db.execute(
        "SELECT p.*,a.kind,a.document_id FROM finance_payments p "
        "JOIN finance_installments i ON i.id=p.installment_id "
        "JOIN accounts a ON a.id=i.account_id WHERE p.id=?",
        (identifier,),
    ).fetchone()
    if not row or row["kind"] != "refund" or row["reversed"]:
        raise ERPError("cash_refund_payment", "Escolha uma baixa de reembolso ativa.")
    own = db.execute(
        "SELECT order_id FROM return_refund_requests WHERE id=?", (row["document_id"],)
    ).fetchone()
    order_id = own[0] if own else row["document_id"]
    doc = db.execute(
        "SELECT data FROM documents WHERE id=? AND kind='order'", (order_id,)
    ).fetchone()
    if not doc:
        raise ERPError("cash_refund_order", "O reembolso não possui pedido próprio identificado.")
    return row, order_id, json.loads(doc[0])


def apply(erp, db, kind, payload, row, data, actor):
    from app.erp_local import cash_register, payment_methods

    if kind == "cash.refund.reverse":
        refund = db.execute(
            "SELECT * FROM cash_refunds WHERE id=? AND session_id=? AND reversed=0",
            (payload.get("refund_id"), row["id"]),
        ).fetchone()
        if not refund:
            raise ERPError("cash_refund_missing", "Classificação ativa não encontrada neste caixa.")
        details = json.loads(refund["data"])
        details.update(
            reversed_at=erp.now(),
            reversed_by=actor,
            reversal_reason=text(payload.get("reason"), "motivo do estorno"),
        )
        db.execute(
            "UPDATE cash_refunds SET reversed=1,data=? WHERE id=?", (dumps(details), refund["id"])
        )
        return cash_register.persist(erp, db, row, data)
    paid, order_id, order = payment(db, payload.get("payment_id"))
    if (order["channel"], order["store"]) != (data["channel"], data["store"]):
        raise ERPError("cash_refund_store", "O reembolso pertence a outra loja.", 409)
    if paid["created_at"] < data["opened_at"]:
        raise ERPError(
            "cash_refund_period",
            "A baixa é anterior à abertura deste caixa. Concilie o período correspondente.",
            409,
        )
    method = payment_methods.freeze(db, payload.get("method_id"))
    if method["type"] != "cash":
        raise ERPError(
            "cash_refund_method",
            "Este recorte classifica devolução em dinheiro. "
            "Outras formas precisam da conciliação específica.",
        )
    if not method.get("bank_id") or method["bank_id"] != paid["bank_id"]:
        raise ERPError(
            "cash_refund_bank", "A conta da baixa deve ser a configurada para este dinheiro."
        )
    amount = number(paid["cash"], money=True)
    if db.execute(
        "SELECT 1 FROM cash_refunds WHERE payment_id=? AND reversed=0", (paid["id"],)
    ).fetchone():
        raise ERPError("cash_refund_duplicate", "A baixa já foi classificada em um caixa.", 409)
    summary = cash_register.summaries(erp, db, row["id"])
    if amount > Decimal(summary["cash_expected"]):
        raise ERPError(
            "cash_insufficient", "O reembolso ultrapassa o dinheiro previsto no caixa.", 409
        )
    original = db.execute(
        "SELECT session_id,status FROM cash_sales WHERE order_id=?", (order_id,)
    ).fetchone()
    if original and original["session_id"] == row["id"] and original["status"] == "void":
        raise ERPError(
            "cash_refund_review",
            "A venda já foi retirada deste fechamento. "
            "Concilie a classificação anterior para não descontar duas vezes.",
            409,
        )
    identifier = str(uuid4())
    db.execute(
        "INSERT INTO cash_refunds VALUES (?,?,?,?,?,0,?)",
        (
            identifier,
            row["id"],
            paid["id"],
            order_id,
            str(amount),
            dumps(
                {
                    "method": method,
                    "reason": text(payload.get("reason"), "motivo"),
                    "actor": actor,
                    "created_at": erp.now(),
                    "number": order["external_id"],
                    "source_cash_id": original["session_id"] if original else None,
                    "bank_id": paid["bank_id"],
                    "principal": paid["principal"],
                }
            ),
        ),
    )
    return {**cash_register.persist(erp, db, row, data), "refund_id": identifier}


def returned_after_close(db, session_data, order_id, order):
    """Only an audited, later physical return can preserve the earlier sales basis."""
    closure = session_data.get("closure") or {}
    for sale in closure.get("sales", []):
        if (
            sale["id"] == order_id
            and sale.get("return_preserves_sale")
            and sale.get("order_version") == order.get("version", 0)
        ):
            return True
    frontier = closure.get("event_seq")
    if type(frontier) is not int:
        return False
    for event in db.execute(
        "SELECT kind,payload FROM events WHERE seq>? "
        "AND kind IN ('order.return','returns.close','returns.receive')",
        (frontier,),
    ):
        payload = json.loads(event["payload"])
        if event["kind"] == "order.return" and payload.get("id") == order_id:
            return True
        if (
            event["kind"] in {"returns.close", "returns.receive"}
            and db.execute(
                "SELECT 1 FROM return_cases WHERE id=? AND order_id=?",
                (payload.get("id"), order_id),
            ).fetchone()
        ):
            return True
    return False


def add_summary(db, identifier, session_row, session_data, grouped, pending, sales):
    refunds = []
    for row in db.execute(
        "SELECT r.*,p.reversed AS payment_reversed,p.cash FROM cash_refunds r "
        "JOIN finance_payments p ON p.id=r.payment_id WHERE r.session_id=? "
        "ORDER BY r.rowid DESC",
        (identifier,),
    ):
        details = json.loads(row["data"])
        refunds.append({**dict(row), "data": details})
        if row["reversed"]:
            continue
        if row["payment_reversed"] or Decimal(row["cash"]) != Decimal(row["amount"]):
            pending.append({"id": row["id"], "reason": "refund_payment_changed"})
        grouped["cash"]["expected"] -= Decimal(row["amount"])
        grouped["cash"]["refunds"] = grouped["cash"].get("refunds", Decimal(0)) + Decimal(
            row["amount"]
        )
    if session_row["status"] == "open":
        own_orders = {sale["order_id"] for sale in sales if sale["status"] != "void"}
        for row in db.execute(
            "SELECT p.id,p.created_at,a.document_id FROM finance_payments p "
            "JOIN finance_installments i ON i.id=p.installment_id "
            "JOIN accounts a ON a.id=i.account_id WHERE a.kind='refund' "
            "AND p.reversed=0 AND p.created_at>=? AND NOT EXISTS "
            "(SELECT 1 FROM cash_refunds r WHERE r.payment_id=p.id AND r.reversed=0)",
            (session_data["opened_at"],),
        ):
            own = db.execute(
                "SELECT order_id FROM return_refund_requests WHERE id=?", (row["document_id"],)
            ).fetchone()
            order_id = own[0] if own else row["document_id"]
            if order_id in own_orders:
                pending.append({"id": row["id"], "reason": "refund_payment_unclassified"})
    return refunds


def candidates(db, session_data):
    result = []
    for row in db.execute(
        "SELECT p.id FROM finance_payments p JOIN finance_installments i "
        "ON i.id=p.installment_id JOIN accounts a ON a.id=i.account_id "
        "WHERE a.kind='refund' AND p.reversed=0 AND p.created_at>=? "
        "AND NOT EXISTS (SELECT 1 FROM cash_refunds r WHERE r.payment_id=p.id "
        "AND r.reversed=0) ORDER BY p.created_at DESC",
        (session_data["opened_at"],),
    ):
        try:
            paid, _, order = payment(db, row["id"])
        except ERPError:
            continue
        if (order["channel"], order["store"]) == (session_data["channel"], session_data["store"]):
            result.append(
                {
                    "id": row["id"],
                    "number": order["external_id"],
                    "amount": paid["cash"],
                    "bank_id": paid["bank_id"],
                    "created_at": paid["created_at"],
                }
            )
    return result
