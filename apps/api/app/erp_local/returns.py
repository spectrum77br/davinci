"""Own physical return cases and explicit refund obligations.

Receiving pieces never buys reverse freight or pays a refund. Carrier status
does not prove receipt. Virtual kits are received as their frozen components.
"""

import json
from decimal import Decimal
from uuid import uuid4

from app.erp_local import deposits, finance
from app.erp_local.core import ERPError, dumps, number, text

SCHEMA = """
CREATE TABLE IF NOT EXISTS return_cases (
 id TEXT PRIMARY KEY, order_id TEXT REFERENCES documents(id), cycle INTEGER NOT NULL,
 status TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(order_id,cycle));
CREATE TABLE IF NOT EXISTS return_receipts (
 id TEXT PRIMARY KEY, case_id TEXT REFERENCES return_cases(id),
 order_id TEXT REFERENCES documents(id), external_key TEXT UNIQUE,
 status TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS return_refund_requests (
 id TEXT PRIMARY KEY REFERENCES documents(id), order_id TEXT REFERENCES documents(id),
 external_key TEXT UNIQUE, fingerprint TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS return_cases_one_open
 ON return_cases(order_id) WHERE status='open';
CREATE INDEX IF NOT EXISTS return_receipts_order ON return_receipts(order_id,status);
CREATE INDEX IF NOT EXISTS return_refunds_order ON return_refund_requests(order_id);
"""
FIELDS = {
    "returns.open": {"id", "reason"},
    "returns.receive": {"id", "receipt_id", "lines", "notes"},
    "returns.reverse": {"id"},
    "returns.cancel": {"id"},
    "returns.close": {"id", "financial"},
    "returns.refund": {"id", "reference", "amount", "reason"},
}
COMMANDS = set(FIELDS)


def case(db, identifier):
    row = db.execute("SELECT * FROM return_cases WHERE id=?", (identifier,)).fetchone()
    if not row:
        raise ERPError("return_case_not_found", "Devolução própria não encontrada.", 404)
    return dict(row), json.loads(row["data"])


def received(db, identifier):
    totals = {}
    for row in db.execute(
        "SELECT data FROM return_receipts WHERE order_id=? AND status='received'", (identifier,)
    ):
        for item in json.loads(row[0])["lines"]:
            totals[item["sku"]] = totals.get(item["sku"], Decimal(0)) + Decimal(item["quantity"])
    return totals


def remaining(db, identifier, order):
    done = received(db, identifier)
    return {sku: Decimal(q) - done.get(sku, Decimal(0)) for sku, q in order["requirements"].items()}


def has_receipts(db, identifier):
    return bool(
        db.execute(
            "SELECT 1 FROM return_receipts WHERE order_id=? AND status='received'", (identifier,)
        ).fetchone()
    )


def refunded_total(db, identifier):
    """Outstanding and paid refund promises both consume the refund ceiling."""
    own = sum(
        (
            Decimal(r[0])
            for r in db.execute(
                "SELECT a.amount FROM return_refund_requests r "
                "JOIN accounts a ON a.document_id=r.id "
                "WHERE r.order_id=? AND a.status!='cancelled'",
                (identifier,),
            )
        ),
        Decimal(0),
    )
    legacy = sum(
        (
            Decimal(r[0])
            for r in db.execute(
                "SELECT amount FROM accounts WHERE document_id=? "
                "AND kind='refund' AND status!='cancelled'",
                (identifier,),
            )
        ),
        Decimal(0),
    )
    return own + legacy


def active_refund(db, identifier):
    return bool(
        db.execute(
            "SELECT 1 FROM return_refund_requests r JOIN accounts a ON a.document_id=r.id "
            "WHERE r.order_id=? AND a.status!='cancelled'",
            (identifier,),
        ).fetchone()
    )


def principal_paid(db, identifier):
    return sum(
        (
            finance.paid(db, r[0])
            for r in db.execute(
                "SELECT i.id FROM finance_installments i JOIN accounts a ON a.id=i.account_id "
                "WHERE a.document_id=? AND a.kind='receivable'",
                (identifier,),
            )
        ),
        Decimal(0),
    )


def apply(erp, db, key, kind, p):
    if not isinstance(p, dict) or set(p) - FIELDS[kind]:
        raise ERPError("return_fields", "Campos de devolução inválidos.")
    if kind == "returns.refund":
        return refund(erp, db, p)
    if kind == "returns.reverse":
        return reverse(erp, db, key, p)
    if kind == "returns.open":
        doc, order = erp.document(db, p.get("id"), "order")
        reason = text(p.get("reason"), "motivo", 500)
        if doc["status"] != "shipped":
            raise ERPError("return_order_status", "A devolução exige saída física registrada.", 409)
        previous = db.execute(
            "SELECT id FROM return_cases WHERE order_id=? AND status='open'", (doc["id"],)
        ).fetchone()
        if previous:
            return {"id": previous[0], "unchanged": True}
        if not any(remaining(db, doc["id"], order).values()):
            raise ERPError(
                "return_complete", "Todas as peças deste pedido já foram recebidas.", 409
            )
        cycle = db.execute(
            "SELECT coalesce(max(cycle),0)+1 FROM return_cases WHERE order_id=?", (doc["id"],)
        ).fetchone()[0]
        identifier = str(uuid4())
        db.execute(
            "INSERT INTO return_cases VALUES (?,?,?,?,?,?)",
            (identifier, doc["id"], cycle, "open", dumps({"reason": reason}), erp.now()),
        )
        return {"id": identifier, "external_writes": False}
    row, data = case(db, p.get("id"))
    doc, order = erp.document(db, row["order_id"], "order")
    if kind == "returns.cancel":
        if row["status"] == "cancelled":
            return {"id": row["id"], "unchanged": True}
        if (
            row["status"] != "open"
            or db.execute(
                "SELECT 1 FROM return_receipts WHERE case_id=? AND status='received'", (row["id"],)
            ).fetchone()
            or active_refund(db, doc["id"])
        ):
            raise ERPError(
                "return_cancel_review",
                "Estorne as entradas e concilie reembolsos antes de cancelar a devolução.",
                409,
            )
        db.execute("UPDATE return_cases SET status='cancelled' WHERE id=?", (row["id"],))
        return {"id": row["id"], "external_writes": False}
    if kind == "returns.receive":
        return receive(erp, db, key, row, doc, order, p)
    if row["status"] not in {"open", "closed"}:
        raise ERPError("return_transition", "Esta devolução não pode ser concluída.", 409)
    financial = p.get("financial")
    if financial not in {"hold", "cancel_sale"}:
        raise ERPError(
            "return_financial", "Escolha preservar contas ou cancelar a venda no financeiro."
        )
    if row["status"] == "closed" and data.get("financial") == financial:
        return {"id": row["id"], "unchanged": True}
    if data.get("financial") == "cancel_sale":
        raise ERPError(
            "return_financial", "A origem financeira já foi cancelada; concilie o histórico.", 409
        )
    if not db.execute(
        "SELECT 1 FROM return_receipts WHERE case_id=? AND status='received'", (row["id"],)
    ).fetchone():
        raise ERPError("return_empty", "Registre o recebimento físico antes de concluir.", 409)
    complete = not any(remaining(db, doc["id"], order).values())
    if not complete and financial != "hold":
        raise ERPError(
            "return_partial_finance", "Devolução parcial exige decisão de reembolso separada.", 409
        )
    if financial == "cancel_sale":
        from app.erp_local.sales import cancel_receivable

        cancel_receivable(erp, db, doc["id"])
    data.update(financial=financial, closed_at=erp.now(), complete=complete)
    db.execute(
        "UPDATE return_cases SET status='closed',data=? WHERE id=?", (dumps(data), row["id"])
    )
    order.update(
        situation="returned" if complete else "partial_return", version=order.get("version", 0) + 1
    )
    if complete:
        order["stock_posted"] = False
    erp.update_document(db, doc["id"], "returned" if complete else doc["status"], order)
    if complete:
        shipment = db.execute(
            "SELECT data FROM logistics_shipments WHERE id=?", (doc["id"],)
        ).fetchone()
        if shipment:
            shipping = json.loads(shipment[0])
            shipping["return"] = {
                "reason": data["reason"],
                "status": "received",
                "received_at": erp.now(),
                "own_case_id": row["id"],
            }
            db.execute(
                "UPDATE logistics_shipments SET stage='returned',data=? WHERE id=?",
                (dumps(shipping), doc["id"]),
            )
    return {"id": row["id"], "complete": complete, "financial": financial, "external_writes": False}


def receive(erp, db, key, row, doc, order, p):
    reference = text(p.get("receipt_id"), "comprovante físico")
    external = dumps([row["order_id"], reference])
    raw = p.get("lines")
    if not isinstance(raw, list) or not 1 <= len(raw) <= 200:
        raise ERPError("return_lines", "Informe as peças físicas recebidas.")
    lines, seen = [], set()
    for item in raw:
        if not isinstance(item, dict) or set(item) != {
            "sku",
            "quantity",
            "condition",
            "destination",
        }:
            raise ERPError("return_lines", "Informe peça, quantidade, inspeção e depósito.")
        sku = text(item["sku"], "SKU")
        if sku not in order["requirements"]:
            raise ERPError("return_sku", "Peça repetida ou não pertencente à saída do pedido.")
        quantity = number(item["quantity"])
        destination = erp.depot(db, item["destination"])
        settings = deposits.metadata(db, deposits.require_active(db, destination))
        condition = item["condition"]
        if not isinstance(condition, str) or condition not in {"new", "damaged"}:
            raise ERPError("return_condition", "Informe a condição de cada peça recebida.")
        identity = (sku, condition, destination)
        if identity in seen:
            raise ERPError(
                "return_sku", "Some a quantidade das peças com a mesma inspeção e depósito."
            )
        seen.add(identity)
        if condition == "damaged" and settings["purpose"] != "quarantine":
            raise ERPError(
                "return_quarantine", "Avarias devem ser recebidas em depósito de quarentena.", 409
            )
        if condition == "new" and settings["purpose"] == "quarantine":
            raise ERPError(
                "return_destination", "Use inspeção Avariado para manter a peça fora da venda."
            )
        lines.append(
            {
                "sku": sku,
                "quantity": str(quantity),
                "condition": condition,
                "destination": destination,
            }
        )
    notes = text(p["notes"], "observações", 2000) if p.get("notes") else ""
    fingerprint = dumps(
        {
            "lines": sorted(lines, key=lambda i: (i["sku"], i["condition"], i["destination"])),
            "notes": notes,
        }
    )
    previous = db.execute(
        "SELECT * FROM return_receipts WHERE external_key=?", (external,)
    ).fetchone()
    if previous:
        previous_data = json.loads(previous["data"])
        if previous_data["input"] != fingerprint or previous["case_id"] != row["id"]:
            raise ERPError("return_receipt_conflict", "Comprovante já usado com outros dados.", 409)
        return {"id": previous["id"], "duplicate": True, "status": previous["status"]}
    if row["status"] != "open" or doc["status"] != "shipped":
        raise ERPError("return_transition", "Esta devolução não está aberta para recebimento.", 409)
    pending = remaining(db, doc["id"], order)
    totals = {}
    for item in lines:
        totals[item["sku"]] = totals.get(item["sku"], Decimal(0)) + Decimal(item["quantity"])
    if any(q > pending[sku] for sku, q in totals.items()):
        raise ERPError(
            "return_overreceipt", "Quantidade recebida ultrapassa a saída restante.", 409
        )
    for item in lines:
        quantity = Decimal(item["quantity"])
        if quantity > pending[item["sku"]]:
            raise ERPError(
                "return_overreceipt", "Quantidade recebida ultrapassa a saída restante.", 409
            )
        cost = order.get("shipped_costs", {}).get(item["sku"]) or "unknown"
        erp.move(
            db,
            key,
            item["sku"],
            item["destination"],
            quantity,
            Decimal(0),
            "recebimento devolução " + order["external_id"],
            cost,
        )
        item["unit_cost"] = cost
    identifier = str(uuid4())
    db.execute(
        "INSERT INTO return_receipts VALUES (?,?,?,?,?,?,?)",
        (
            identifier,
            row["id"],
            doc["id"],
            external,
            "received",
            dumps({"reference": reference, "lines": lines, "notes": notes, "input": fingerprint}),
            erp.now(),
        ),
    )
    order.update(situation="partial_return", version=order.get("version", 0) + 1)
    erp.update_document(db, doc["id"], doc["status"], order)
    return {
        "id": identifier,
        "complete": not any(remaining(db, doc["id"], order).values()),
        "external_writes": False,
    }


def reverse(erp, db, key, p):
    receipt = db.execute("SELECT * FROM return_receipts WHERE id=?", (p.get("id"),)).fetchone()
    if not receipt:
        raise ERPError("return_receipt_not_found", "Recebimento de devolução não encontrado.", 404)
    if receipt["status"] == "reversed":
        return {"id": receipt["id"], "unchanged": True}
    row, _ = case(db, receipt["case_id"])
    if row["status"] != "open" or active_refund(db, receipt["order_id"]):
        raise ERPError(
            "return_reverse_review",
            "Concilie a conclusão e os reembolsos antes de estornar a entrada.",
            409,
        )
    for item in json.loads(receipt["data"])["lines"]:
        erp.move(
            db,
            key,
            item["sku"],
            item["destination"],
            -Decimal(item["quantity"]),
            Decimal(0),
            "estorno recebimento devolução",
            item["unit_cost"],
        )
    db.execute("UPDATE return_receipts SET status='reversed' WHERE id=?", (receipt["id"],))
    doc, order = erp.document(db, receipt["order_id"], "order")
    order.update(
        situation="partial_return" if has_receipts(db, doc["id"]) else "shipped",
        version=order.get("version", 0) + 1,
    )
    erp.update_document(db, doc["id"], doc["status"], order)
    return {"id": receipt["id"], "status": "reversed", "external_writes": False}


def refund(erp, db, p):
    doc, order = erp.document(db, p.get("id"), "order")
    reference, reason = text(p.get("reference"), "referência"), text(p.get("reason"), "motivo", 500)
    amount = number(p.get("amount"), money=True)
    external = dumps([doc["id"], reference])
    fingerprint = dumps([str(amount), reason])
    previous = db.execute(
        "SELECT * FROM return_refund_requests WHERE external_key=?", (external,)
    ).fetchone()
    if previous:
        if previous["fingerprint"] != fingerprint:
            raise ERPError(
                "return_refund_conflict", "Referência de reembolso já usada com outros dados.", 409
            )
        return {"id": previous["id"], "duplicate": True}
    if not has_receipts(db, doc["id"]):
        raise ERPError(
            "return_refund_review", "Registre as peças recebidas antes de aprovar o reembolso.", 409
        )
    available = principal_paid(db, doc["id"]) - refunded_total(db, doc["id"])
    if amount > available:
        raise ERPError(
            "return_refund_limit",
            "Reembolso ultrapassa o principal recebido ainda não comprometido.",
            409,
        )
    identifier = str(uuid4())
    data = {
        "reference": reference,
        "party": order.get("customer_name", "Cliente do pedido"),
        "history": reason,
        "amount": str(amount),
        "return_order_id": doc["id"],
        "kind": "refund",
        "issued_on": finance.today(),
        "competence_on": finance.today(),
        "due_date": finance.today(),
        "occurrence": "once",
        "count": 1,
    }
    db.execute(
        "INSERT INTO documents VALUES (?,?,?,?,?)",
        (
            identifier,
            "financial",
            dumps(["return_refund", doc["id"], reference]),
            "open",
            dumps(data),
        ),
    )
    erp.account(db, identifier, "refund", str(amount))
    db.execute(
        "INSERT INTO return_refund_requests VALUES (?,?,?,?,?)",
        (identifier, doc["id"], external, fingerprint, erp.now()),
    )
    return {"id": identifier, "external_payments": False, "external_writes": False}


def snapshot(erp, db):
    cases = []
    for row in db.execute("SELECT * FROM return_cases ORDER BY rowid DESC"):
        doc, order = erp.document(db, row["order_id"], "order")
        cases.append(
            {
                **dict(row),
                "data": json.loads(row["data"]),
                "sale_number": order["external_id"],
                "order_status": doc["status"],
                "remaining": {k: str(v) for k, v in remaining(db, doc["id"], order).items()},
                "refund_available": str(
                    max(Decimal(0), principal_paid(db, doc["id"]) - refunded_total(db, doc["id"]))
                ),
            }
        )
    return {
        "cases": cases,
        "receipts": [
            {**dict(r), "data": json.loads(r["data"])}
            for r in db.execute("SELECT * FROM return_receipts ORDER BY rowid DESC")
        ],
        "refunds": [
            dict(r)
            for r in db.execute(
                "SELECT r.*,d.status,d.data FROM return_refund_requests r "
                "JOIN documents d ON d.id=r.id ORDER BY r.rowid DESC"
            )
        ],
        "external_payments": False,
    }
