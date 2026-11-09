"""Partial receipts and explicit landed costs; no tax-rate inference or supplier writes."""

import json
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from uuid import uuid4

from app.erp_local import purchase_documents
from app.erp_local.core import ERPError, dumps, number, text
from app.erp_local.finance import paid

SCHEMA = """
CREATE TABLE IF NOT EXISTS purchase_receipts (
 id TEXT PRIMARY KEY, document_id TEXT REFERENCES documents(id), external_key TEXT UNIQUE,
 input TEXT NOT NULL, data TEXT NOT NULL, status TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS inventory_values (
 sku TEXT REFERENCES products(sku), depot TEXT REFERENCES deposits(id), value TEXT,
 PRIMARY KEY(sku,depot));
CREATE TABLE IF NOT EXISTS inventory_cost_entries (
 id INTEGER PRIMARY KEY AUTOINCREMENT, operation_key TEXT REFERENCES events(operation_key),
 sku TEXT, depot TEXT, quantity TEXT, value_delta TEXT, unit_cost TEXT, created_at TEXT);
"""
SCHEMA += purchase_documents.SCHEMA

COMMANDS = {
    "purchases.batch",
    "purchase.update",
    "purchase.cancel",
    "purchase.reopen",
    "purchase.create",
    "purchase.stock",
    "purchase.receive",
    "purchase.reverse_receipt",
    "purchase.reverse_stock",
    "purchase.accounts",
    "purchase.reverse_accounts",
    "valuation.set",
}


def pool(erp, db, sku, depot):
    quantity = erp.balance(db, sku, depot)[0]
    row = db.execute(
        "SELECT value FROM inventory_values WHERE sku=? AND depot=?", (sku, depot)
    ).fetchone()
    if row:
        return Decimal(row[0]) if row[0] is not None else None
    price = db.execute("SELECT cost_price FROM product_pricing WHERE sku=?", (sku,)).fetchone()
    return (
        quantity * Decimal(price[0])
        if price and price[0] is not None and quantity >= 0
        else Decimal(0)
        if quantity == 0
        else None
    )


def average(erp, db, sku, depot):
    quantity = erp.balance(db, sku, depot)[0]
    value = pool(erp, db, sku, depot)
    return value / quantity if value is not None and quantity > 0 else None


def track_cost(erp, db, key, sku, depot, old_physical, delta, unit_cost):
    if not delta:
        return
    row = db.execute(
        "SELECT value FROM inventory_values WHERE sku=? AND depot=?", (sku, depot)
    ).fetchone()
    price = db.execute("SELECT cost_price FROM product_pricing WHERE sku=?", (sku,)).fetchone()
    value = Decimal(row[0]) if row and row[0] is not None else None
    if not row:
        value = (
            old_physical * Decimal(price[0])
            if price and price[0] is not None and old_physical >= 0
            else Decimal(0)
            if old_physical == 0
            else None
        )
    cost = (
        None
        if unit_cost == "unknown"
        else (
            Decimal(str(unit_cost))
            if unit_cost is not None
            else value / old_physical
            if value is not None and old_physical > 0
            else Decimal(price[0])
            if price and price[0] is not None
            else None
        )
    )
    change = delta * cost if cost is not None else None
    updated = (
        value + change
        if value is not None and change is not None and old_physical + delta >= 0
        else None
    )
    if updated is not None:
        updated = updated.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if value is not None:
            change = updated - value
    if updated is not None and updated < 0:
        raise ERPError(
            "valuation_review",
            "O estorno deixa valor de estoque negativo; concilie os custos.",
            409,
        )
    if old_physical + delta == 0:
        updated = Decimal(0)
    db.execute(
        "INSERT INTO inventory_values VALUES (?,?,?) ON CONFLICT(sku,depot) "
        "DO UPDATE SET value=excluded.value",
        (sku, depot, str(updated) if updated is not None else None),
    )
    db.execute(
        "INSERT INTO inventory_cost_entries(operation_key,sku,depot,quantity,value_delta,"
        "unit_cost,created_at) VALUES (?,?,?,?,?,?,?)",
        (
            key,
            sku,
            depot,
            str(delta),
            str(change) if change is not None else None,
            str(cost) if cost is not None else None,
            erp.now(),
        ),
    )


def allocate(amount, weights):
    """Largest-remainder cents allocation: deterministic, with no lost cent."""
    cents = int(amount * 100)
    weight = sum(weights, Decimal(0))
    if not weight:
        weights, weight = [Decimal(1)] * len(weights), Decimal(len(weights))
    exact = [Decimal(cents) * w / weight for w in weights]
    units = [int(v.to_integral_value(rounding=ROUND_FLOOR)) for v in exact]
    for i in sorted(range(len(units)), key=lambda i: (-(exact[i] - units[i]), i))[
        : cents - sum(units)
    ]:
        units[i] += 1
    return [Decimal(n) / 100 for n in units]


def receipt_totals(db, identifier, size):
    quantities = [Decimal(0)] * size
    for row in db.execute(
        "SELECT data FROM purchase_receipts WHERE document_id=? AND status='received'",
        (identifier,),
    ):
        for part in json.loads(row[0])["lines"]:
            quantities[part["line"]] += Decimal(part["quantity"])
    return quantities


def update_status(erp, db, identifier, data):
    received = receipt_totals(db, identifier, len(data["items"]))
    # Old full receipts predate the receipt ledger. Financial actions must not reset them.
    if (
        data.get("stock_posted")
        and not db.execute(
            "SELECT 1 FROM purchase_receipts WHERE document_id=?", (identifier,)
        ).fetchone()
    ):
        received = [Decimal(i["quantity"]) for i in data["items"]]
    data["received"] = [str(n) for n in received]
    data["stock_posted"] = all(
        q == Decimal(i["quantity"]) for q, i in zip(received, data["items"], strict=True)
    )
    status = (
        "received"
        if data["stock_posted"] and data["accounts_posted"]
        else "partial"
        if any(received) or data["accounts_posted"]
        else "open"
    )
    erp.update_document(db, identifier, status, data)
    return status


def receive(erp, db, key, p):
    doc, data = erp.document(db, p.get("id"), "purchase")
    if doc["status"] == "cancelled":
        raise ERPError("purchase_cancelled", "Reabra a compra antes de lançar seus efeitos.", 409)
    if (
        data.get("stock_posted")
        and not db.execute(
            "SELECT 1 FROM purchase_receipts WHERE document_id=?", (doc["id"],)
        ).fetchone()
    ):
        raise ERPError(
            "legacy_receipt_review",
            "A compra anterior ao controle parcial já foi recebida; "
            "concilie seu histórico antes de lançar novamente.",
            409,
        )
    external = dumps([doc["id"], text(p.get("receipt_id"), "identificador do recebimento")])
    raw = p.get("lines")
    if not isinstance(raw, list) or not raw or len(raw) > 200:
        raise ERPError("invalid_receipt", "Informe os itens recebidos.")
    lines = []
    for part in raw:
        if (
            not isinstance(part, dict)
            or set(part) - {"line", "quantity", "lot"}
            or type(part.get("line")) is not int
            or not 0 <= part["line"] < len(data["items"])
        ):
            raise ERPError("invalid_receipt", "Linha de recebimento inválida.")
        lines.append(
            {
                "line": part["line"],
                "quantity": str(number(part.get("quantity"))),
                "lot": text(part["lot"], "lote") if part.get("lot") else None,
            }
        )
    if len({i["line"] for i in lines}) != len(lines):
        raise ERPError("duplicate_receipt_line", "Agrupe cada item numa linha.")
    lines.sort(key=lambda i: i["line"])
    previous = db.execute(
        "SELECT * FROM purchase_receipts WHERE external_key=?", (external,)
    ).fetchone()
    if previous:
        if previous["input"] != dumps(lines):
            raise ERPError("receipt_conflict", "Recebimento já cadastrado com outros dados.", 409)
        return {"id": previous["id"], "duplicate": True, "status": previous["status"]}
    received = receipt_totals(db, doc["id"], len(data["items"]))
    booked = [Decimal(0)] * len(data["items"])
    for row in db.execute(
        "SELECT data FROM purchase_receipts WHERE document_id=? AND status='received'", (doc["id"],)
    ):
        for line in json.loads(row[0])["lines"]:
            booked[line["line"]] += Decimal(
                line.get("cost_total", str(Decimal(line["quantity"]) * Decimal(line["unit_cost"])))
            )
    for part in lines:
        item = data["items"][part["line"]]
        q = Decimal(part["quantity"])
        if received[part["line"]] + q > Decimal(item["quantity"]):
            raise ERPError("over_receipt", "O recebimento ultrapassa a quantidade da compra.", 409)
        line_total = (
            Decimal(data["landed_totals"][part["line"]])
            if data.get("landed_totals")
            else Decimal(item["quantity"]) * Decimal(item["unit_price"])
        )
        target = (line_total * (received[part["line"]] + q) / Decimal(item["quantity"])).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        receipt_value = target - booked[part["line"]]
        if receipt_value < 0:
            raise ERPError(
                "receipt_cost_review", "Concilie o rateio dos recebimentos anteriores.", 409
            )
        cost = receipt_value / q
        part.update(sku=item["sku"], unit_cost=str(cost), cost_total=str(receipt_value))
        erp.move(
            db, key, item["sku"], data["depot"], q, Decimal(0), "recebimento de compra", str(cost)
        )
    identifier = str(uuid4())
    canonical = [{k: part[k] for k in ("line", "quantity", "lot")} for part in lines]
    db.execute(
        "INSERT INTO purchase_receipts VALUES (?,?,?,?,?,'received')",
        (
            identifier,
            doc["id"],
            external,
            dumps(canonical),
            dumps({"lines": lines, "depot": data["depot"], "created_at": erp.now()}),
        ),
    )
    status = update_status(erp, db, doc["id"], data)
    return {"id": identifier, "purchase_id": doc["id"], "status": status, "duplicate": False}


def reverse_receipt(erp, db, key, identifier):
    row = db.execute("SELECT * FROM purchase_receipts WHERE id=?", (identifier,)).fetchone()
    if not row:
        raise ERPError("receipt_not_found", "Recebimento não encontrado.", 404)
    if row["status"] == "reversed":
        return {"id": identifier, "unchanged": True}
    details = json.loads(row["data"])
    for part in details["lines"]:
        erp.move(
            db,
            key,
            part["sku"],
            details["depot"],
            -Decimal(part["quantity"]),
            Decimal(0),
            "estorno de recebimento",
            part["unit_cost"],
        )
    db.execute("UPDATE purchase_receipts SET status='reversed' WHERE id=?", (identifier,))
    doc, data = erp.document(db, row["document_id"], "purchase")
    update_status(erp, db, doc["id"], data)
    return {"id": identifier, "status": "reversed"}


def apply(erp, db, key, kind, p):
    if kind == "purchases.batch":
        if (
            not isinstance(p, dict)
            or set(p) != {"ids", "action"}
            or p["action"] not in {"stock", "reverse_stock", "accounts", "reverse_accounts"}
        ):
            raise ERPError("purchase_batch", "Ação do lote de compras inválida.")
        identifiers = p["ids"]
        if (
            not isinstance(identifiers, list)
            or not 1 <= len(identifiers) <= 200
            or any(not isinstance(i, str) or not i for i in identifiers)
            or len(set(identifiers)) != len(identifiers)
        ):
            raise ERPError("purchase_batch", "Selecione de 1 a 200 compras distintas.")
        # The enclosing ERP command owns one transaction. Any failed purchase
        # rolls back the entire selection, including its receipts and accounts.
        results = [apply(erp, db, key, "purchase." + p["action"], {"id": i}) for i in identifiers]
        return {"purchases": results, "action": p["action"], "external_writes": False}
    allowed = {
        "purchase.create": purchase_documents.FIELDS,
        "purchase.update": purchase_documents.FIELDS | {"id", "version", "reason"},
        "purchase.cancel": {"id", "version", "reason"},
        "purchase.reopen": {"id", "version", "reason"},
        "purchase.receive": {"id", "receipt_id", "lines"},
        "valuation.set": {"sku", "depot", "unit_cost", "reason"},
    }
    if set(p) - allowed.get(kind, {"id"}):
        raise ERPError("unsupported_fields", "Campos da compra não suportados.")
    if kind == "valuation.set":
        sku = erp.product(db, p.get("sku"))["sku"]
        depot = erp.depot(db, p.get("depot"))
        if erp.product(db, sku)["kind"] != "simple":
            raise ERPError("virtual_kit", "O custo do kit é composto pelas peças.")
        text(p.get("reason"), "motivo")
        physical = erp.balance(db, sku, depot)[0]
        if physical < 0 or (erp.origin(db, sku) or {}).get("stock_state", "known") != "known":
            raise ERPError("valuation_review", "Concilie o saldo antes do custo.", 409)
        cost = number(p.get("unit_cost"), unit=True, zero=True)
        value = physical * cost
        db.execute(
            "INSERT INTO inventory_values VALUES (?,?,?) ON CONFLICT(sku,depot) "
            "DO UPDATE SET value=excluded.value",
            (sku, depot, str(value)),
        )
        return {"sku": sku, "value": str(value)}
    if kind in {"purchase.create", "purchase.update"}:
        return purchase_documents.save(erp, db, key, kind, p)
    if kind in {"purchase.cancel", "purchase.reopen"}:
        return purchase_documents.change_state(erp, db, key, kind, p)
    if kind == "purchase.receive":
        return receive(erp, db, key, p)
    if kind == "purchase.reverse_receipt":
        return reverse_receipt(erp, db, key, p.get("id"))
    doc, data = erp.document(db, p.get("id"), "purchase")
    if doc["status"] == "cancelled":
        raise ERPError("purchase_cancelled", "Reabra a compra antes de lançar seus efeitos.", 409)
    if kind == "purchase.stock":
        if data["stock_posted"]:
            return {"id": doc["id"], "unchanged": True}
        quantities = receipt_totals(db, doc["id"], len(data["items"]))
        receive(
            erp,
            db,
            key,
            {
                "id": doc["id"],
                "receipt_id": key,
                "lines": [
                    {"line": i, "quantity": str(Decimal(item["quantity"]) - quantities[i])}
                    for i, item in enumerate(data["items"])
                    if Decimal(item["quantity"]) > quantities[i]
                ],
            },
        )
        return {"id": doc["id"], "status": erp.document(db, doc["id"], "purchase")[0]["status"]}
    if kind == "purchase.reverse_stock":
        rows = db.execute(
            "SELECT id FROM purchase_receipts WHERE document_id=? AND status='received' "
            "ORDER BY rowid DESC",
            (doc["id"],),
        ).fetchall()
        if not rows and data["stock_posted"]:
            raise ERPError(
                "legacy_receipt_review",
                "Recebimento anterior ao controle parcial exige conciliação.",
                409,
            )
        for row in rows:
            reverse_receipt(erp, db, key, row[0])
        return {"id": doc["id"], "status": erp.document(db, doc["id"], "purchase")[0]["status"]}
    header = db.execute(
        "SELECT * FROM accounts WHERE document_id=? AND kind='payable'", (doc["id"],)
    ).fetchone()
    if kind == "purchase.reverse_accounts":
        if header and (
            header["status"] in {"paid", "partial"}
            or any(
                paid(db, r[0])
                for r in db.execute(
                    "SELECT id FROM finance_installments WHERE account_id=?", (header["id"],)
                )
            )
        ):
            raise ERPError("paid_account", "Estorne as baixas antes de estornar a conta.", 409)
        if header:
            db.execute("UPDATE accounts SET status='cancelled' WHERE id=?", (header["id"],))
            db.execute(
                "UPDATE finance_installments SET active=0 WHERE account_id=?", (header["id"],)
            )
        data["accounts_posted"] = False
    else:
        if data["accounts_posted"]:
            return {"id": doc["id"], "unchanged": True}
        if header:
            db.execute(
                "UPDATE accounts SET status='open',amount=? WHERE id=?",
                (data["total"], header["id"]),
            )
        else:
            erp.account(db, doc["id"], "payable", data["total"])
        data["accounts_posted"] = True
    return {"id": doc["id"], "status": update_status(erp, db, doc["id"], data)}


def snapshot(erp, db):
    return {
        "history": [
            {**dict(r), "data": json.loads(r["data"])}
            for r in db.execute("SELECT * FROM purchase_document_history ORDER BY rowid DESC")
        ],
        "receipts": [
            {**dict(r), "data": json.loads(r["data"])}
            for r in db.execute("SELECT * FROM purchase_receipts ORDER BY rowid DESC")
        ],
        "values": [
            {
                **dict(r),
                "physical": str(erp.balance(db, r["sku"], r["depot"])[0]),
                "average": str(average(erp, db, r["sku"], r["depot"]))
                if average(erp, db, r["sku"], r["depot"]) is not None
                else None,
            }
            for r in db.execute("SELECT * FROM inventory_values ORDER BY sku,depot")
        ],
    }
