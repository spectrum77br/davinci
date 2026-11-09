"""Versioned purchase terms. Saving never receives goods or pays the supplier."""

import json
from decimal import Decimal
from uuid import uuid4

from app.erp_local.core import ERPError, dumps, number, text, total
from app.erp_local.finance import day, today

FIELDS = {
    "supplier",
    "contact_id",
    "refresh_contact",
    "depot",
    "items",
    "freight",
    "discount",
    "charges",
    "installments",
    "payment_method_id",
    "refresh_payment_method",
    "refresh_supplier_items",
    "number",
    "purchase_order",
    "date",
    "expected_date",
    "notes",
    "internal_notes",
    "carrier_name",
    "freight_payer",
    "packages",
    "gross_weight",
}
SCHEMA = """
CREATE TABLE IF NOT EXISTS purchase_document_history (
 purchase_id TEXT REFERENCES documents(id), version INTEGER NOT NULL, data TEXT NOT NULL,
 reason TEXT NOT NULL, operation_key TEXT REFERENCES events(operation_key), created_at TEXT,
 PRIMARY KEY(purchase_id,version));
"""


def commercial(data):
    return {
        k: v for k, v in data.items() if k not in {"received", "stock_posted", "accounts_posted"}
    }


def history(erp, db, key, identifier, data, reason):
    db.execute(
        "INSERT OR IGNORE INTO purchase_document_history VALUES (?,?,?,?,?,?)",
        (identifier, data.get("version", 0), dumps(commercial(data)), reason, key, erp.now()),
    )


def guard_unposted(db, doc, data):
    if (
        data.get("stock_posted")
        or data.get("accounts_posted")
        or db.execute(
            "SELECT 1 FROM purchase_receipts WHERE document_id=? AND status='received' LIMIT 1",
            (doc["id"],),
        ).fetchone()
    ):
        raise ERPError(
            "purchase_effects",
            "Estorne os recebimentos e as contas antes de alterar a compra.",
            409,
        )
    if db.execute(
        "SELECT 1 FROM accounts WHERE document_id=? AND kind='payable' AND status!='cancelled'",
        (doc["id"],),
    ).fetchone():
        raise ERPError("purchase_effects", "Estorne a conta antes de alterar a compra.", 409)


def save(erp, db, key, kind, p):
    from app.erp_local import contacts, payment_methods, procurement, product_suppliers

    doc, old = (
        erp.document(db, p.get("id"), "purchase") if kind == "purchase.update" else (None, {})
    )
    version = old.get("version", 0)
    if doc:
        if type(p.get("version")) is not int or p["version"] != version:
            raise ERPError(
                "purchase_version", "A compra mudou. Atualize a tela antes de salvar.", 409
            )
        guard_unposted(db, doc, old)
        if doc["status"] == "cancelled":
            raise ERPError("purchase_cancelled", "Reabra a compra antes de editar.", 409)
    reason = text(p.get("reason"), "motivo da alteração") if doc else "Cadastro da compra"
    for flag in ("refresh_contact", "refresh_payment_method", "refresh_supplier_items"):
        if flag in p and type(p[flag]) is not bool:
            raise ERPError("purchase_fields", "Opção de atualização inválida.")
    items = erp.items(db, p.get("items"), simple_only=True)
    amounts = {
        n: number(p.get(n, "0"), money=True, zero=True) for n in ("freight", "discount", "charges")
    }
    subtotal = Decimal(total(items))
    if amounts["discount"] > subtotal:
        raise ERPError("purchase_discount", "Desconto maior que os itens da compra.")
    amount = subtotal + amounts["freight"] + amounts["charges"] - amounts["discount"]
    date = day(p.get("date") or old.get("date") or today())
    expected = day(p["expected_date"]) if p.get("expected_date") else ""
    if expected and expected < date:
        raise ERPError("purchase_date", "A previsão não pode ser anterior à data da compra.")
    contact_id = p.get("contact_id") or ""
    contact = (
        old.get("contact")
        if old.get("contact", {}).get("id") == contact_id and not p.get("refresh_contact")
        else None
    )
    if contact_id and not contact:
        contact = contacts.document_snapshot(db, contact_id)
        registered = json.loads(
            db.execute("SELECT data FROM own_contacts WHERE id=?", (contact_id,)).fetchone()[0]
        )
        if "supplier" not in registered["roles"]:
            raise ERPError(
                "purchase_supplier",
                "Classifique o contato como fornecedor antes de vinculá-lo.",
                409,
            )
    supplier = contact["name"] if contact else text(p.get("supplier"), "fornecedor")
    method_id = p.get("payment_method_id") or ""
    frozen = (
        old.get("payment_method")
        if old.get("payment_method", {}).get("id") == method_id
        and not p.get("refresh_payment_method")
        else None
    )
    if method_id and not frozen:
        frozen = payment_methods.freeze(db, method_id, "payable")
    if frozen and "installments" in p:
        raise ERPError(
            "purchase_terms", "Escolha a forma de pagamento ou parcelas informadas, separadamente."
        )
    if frozen:
        plan, _ = payment_methods.plan(frozen, str(amount), date)
    else:
        plan = (
            p.get("installments")
            if "installments" in p
            else ([{"due_date": date, "amount": str(amount)}] if amount else [])
        )
        if (
            not isinstance(plan, list)
            or len(plan) > 120
            or any(not isinstance(i, dict) or set(i) != {"due_date", "amount"} for i in plan)
        ):
            raise ERPError("invalid_schedule", "Parcelas da compra inválidas.")
        plan = [
            {"due_date": day(i["due_date"]), "amount": str(number(i["amount"], money=True))}
            for i in plan
        ]
    if sum((Decimal(i["amount"]) for i in plan), Decimal(0)) != amount:
        raise ERPError("schedule_total", "A soma das parcelas difere do total da compra.")
    serial = db.execute("SELECT count(*)+1 FROM documents WHERE kind='purchase'").fetchone()[0]
    if not p.get("number") and not old.get("number"):
        while db.execute(
            "SELECT 1 FROM documents WHERE kind='purchase' AND json_extract(data,'$.number')=?",
            (str(serial),),
        ).fetchone():
            serial += 1
    purchase_number = text(p.get("number") or old.get("number") or str(serial), "número da compra")
    if db.execute(
        "SELECT 1 FROM documents WHERE kind='purchase' "
        "AND json_extract(data,'$.number')=? AND id!=?",
        (purchase_number, doc["id"] if doc else ""),
    ).fetchone():
        raise ERPError("purchase_number", "Este número já identifica outra compra.", 409)
    payer = p.get("freight_payer") or "unspecified"
    if payer not in {"supplier", "recipient", "third_party", "none", "unspecified"}:
        raise ERPError("purchase_freight", "Responsável pelo frete inválido.")
    packages = number(p.get("packages") or "0", zero=True)
    if packages != packages.to_integral_value():
        raise ERPError("purchase_packages", "Informe a quantidade inteira de volumes.")
    data = {
        "version": version + 1,
        "number": purchase_number,
        "supplier": supplier,
        "date": date,
        "expected_date": expected,
        "depot": erp.depot(db, p.get("depot")),
        "items": items,
        "total": str(amount),
        "subtotal": str(subtotal),
        "landed_totals": [
            str(v)
            for v in procurement.allocate(
                amount, [Decimal(i["quantity"]) * Decimal(i["unit_price"]) for i in items]
            )
        ],
        **{k: str(v) for k, v in amounts.items()},
        "installments": plan,
        "freight_payer": payer,
        "packages": str(packages),
        "gross_weight": str(number(p.get("gross_weight") or "0", zero=True)),
        "stock_posted": False,
        "accounts_posted": False,
        "received": ["0"] * len(items),
        "document_state": "open",
        "created_at": old.get("created_at") or erp.now(),
        "updated_at": erp.now(),
    }
    for field, limit in [
        ("purchase_order", 120),
        ("notes", 2000),
        ("internal_notes", 2000),
        ("carrier_name", 120),
    ]:
        data[field] = text(p[field], field, limit) if p.get(field) else ""
    if contact:
        data["contact"] = contact
    supplier_items = product_suppliers.purchase_snapshot(
        db,
        items,
        contact_id,
        old.get("supplier_items"),
        refresh=p.get("refresh_supplier_items", False),
    )
    if supplier_items:
        data["supplier_items"] = supplier_items
    if frozen:
        data["payment_method"] = frozen
    identifier = doc["id"] if doc else str(uuid4())
    if doc:
        history(erp, db, key, identifier, old, "Versão anterior preservada")
        erp.update_document(db, identifier, "open", data)
    else:
        db.execute(
            "INSERT INTO documents VALUES (?,?,NULL,?,?)",
            (identifier, "purchase", "open", dumps(data)),
        )
    history(erp, db, key, identifier, data, reason)
    return {
        "id": identifier,
        "version": data["version"],
        "stock_posted": False,
        "accounts_posted": False,
    }


def change_state(erp, db, key, kind, p):
    doc, data = erp.document(db, p.get("id"), "purchase")
    if type(p.get("version")) is not int or p["version"] != data.get("version", 0):
        raise ERPError(
            "purchase_version", "A compra mudou. Atualize antes de alterar a situação.", 409
        )
    guard_unposted(db, doc, data)
    target = "cancelled" if kind == "purchase.cancel" else "open"
    if (target == "open") != (doc["status"] == "cancelled"):
        raise ERPError("purchase_state", "A situação da compra já mudou.", 409)
    reason = text(p.get("reason"), "motivo")
    history(erp, db, key, doc["id"], data, "Versão anterior preservada")
    data.update(document_state=target, version=data.get("version", 0) + 1, updated_at=erp.now())
    erp.update_document(db, doc["id"], target, data)
    history(erp, db, key, doc["id"], data, reason)
    return {"id": doc["id"], "version": data["version"], "status": target}
