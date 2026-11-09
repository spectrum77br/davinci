"""Versioned product/supplier references; catalogue prices never post stock cost.

Client-visible Bling rules: one row per supplier, first/default supplier, raw
supplier code/description, purchase vs landed cost and warranty in months.
Historical purchases retain their reference until explicitly refreshed.
"""

import json

from app.erp_local.core import ERPError, dumps, number, text

SCHEMA = """
CREATE TABLE IF NOT EXISTS product_suppliers (
 sku TEXT PRIMARY KEY REFERENCES products(sku), version INTEGER NOT NULL,
 data TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS product_supplier_history (
 sku TEXT REFERENCES products(sku), version INTEGER NOT NULL, data TEXT NOT NULL,
 reason TEXT NOT NULL, operation_key TEXT REFERENCES events(operation_key), created_at TEXT,
 PRIMARY KEY(sku,version));
"""
COMMANDS = {"product.suppliers.save"}
FIELDS = {
    "contact_id",
    "description",
    "code",
    "purchase_price",
    "cost_price",
    "warranty_months",
    "is_default",
}


def details(db, sku):
    row = db.execute("SELECT version,data FROM product_suppliers WHERE sku=?", (sku,)).fetchone()
    return (
        {"version": row["version"], "rows": json.loads(row["data"])}
        if row
        else {"version": 0, "rows": []}
    )


def snapshot(db):
    return {
        r["sku"]: {"version": r["version"], "rows": json.loads(r["data"])}
        for r in db.execute("SELECT * FROM product_suppliers")
    }


def history(db, sku):
    return [
        {**dict(r), "data": json.loads(r["data"])}
        for r in db.execute(
            "SELECT version,data,reason,created_at FROM product_supplier_history "
            "WHERE sku=? ORDER BY version DESC LIMIT 20",
            (sku,),
        )
    ]


def apply(erp, db, key, payload):
    from app.erp_local.contacts import document_snapshot

    if not isinstance(payload, dict) or set(payload) != {"sku", "version", "rows", "reason"}:
        raise ERPError("supplier_fields", "Informe produto, versão, fornecedores e motivo.")
    sku = erp.product(db, payload["sku"])["sku"]
    current = details(db, sku)
    if type(payload["version"]) is not int or payload["version"] != current["version"]:
        raise ERPError(
            "supplier_version", "Os fornecedores do produto mudaram. Atualize a tela.", 409
        )
    reason = text(payload["reason"], "motivo dos fornecedores", 500)
    raw = payload["rows"]
    if not isinstance(raw, list) or len(raw) > 200:
        raise ERPError("supplier_rows", "Informe até 200 fornecedores por produto.")
    rows, seen, defaults = [], set(), 0
    for source in raw:
        if not isinstance(source, dict) or set(source) != FIELDS:
            raise ERPError("supplier_fields", "Campos do fornecedor não reconhecidos.")
        if any(not isinstance(source[field], str) for field in ["description", "code"]):
            raise ERPError("supplier_fields", "Descrição e código devem ser textos.")
        identifier = text(source["contact_id"], "fornecedor")
        if identifier in seen:
            raise ERPError("supplier_duplicate", "O fornecedor já está vinculado ao produto.", 409)
        seen.add(identifier)
        contact = document_snapshot(db, identifier)
        registered = json.loads(
            db.execute("SELECT data FROM own_contacts WHERE id=?", (identifier,)).fetchone()[0]
        )
        if "supplier" not in registered["roles"]:
            raise ERPError("supplier_role", "Escolha um contato classificado como fornecedor.", 409)
        if type(source["is_default"]) is not bool:
            raise ERPError("supplier_default", "Escolha o fornecedor padrão.")
        defaults += source["is_default"]
        months = source["warranty_months"]
        if type(months) is not int or not 0 <= months <= 999:
            raise ERPError("supplier_warranty", "Garantia deve ser de zero a 999 meses inteiros.")
        item = {
            "contact_id": identifier,
            "contact": {k: contact[k] for k in ["id", "name", "code", "version"]},
            "description": text(source["description"], "descrição no fornecedor", 120)
            if source["description"]
            else "",
            "code": text(source["code"], "código no fornecedor", 120) if source["code"] else "",
            "warranty_months": months,
            "is_default": source["is_default"],
        }
        for field in ["purchase_price", "cost_price"]:
            item[field] = (
                str(number(source[field], unit=True, zero=True))
                if source[field] not in (None, "")
                else None
            )
        rows.append(item)
    if defaults != (1 if rows else 0):
        raise ERPError("supplier_default", "Defina exatamente um fornecedor padrão.")
    version = current["version"] + 1
    db.execute(
        "INSERT INTO product_suppliers VALUES (?,?,?,?) ON CONFLICT(sku) DO UPDATE SET "
        "version=excluded.version,data=excluded.data,updated_at=excluded.updated_at",
        (sku, version, dumps(rows), erp.now()),
    )
    db.execute(
        "INSERT INTO product_supplier_history VALUES (?,?,?,?,?,?)",
        (sku, version, dumps(rows), reason, key, erp.now()),
    )
    return {"sku": sku, "supplier_version": version, "external_writes": False}


def purchase_snapshot(db, items, contact_id, old=None, *, refresh=False):
    if not contact_id:
        return {}
    result = {}
    for item in items:
        sku = item["sku"]
        prior = (old or {}).get(sku)
        if prior and prior["contact_id"] == contact_id and not refresh:
            result[sku] = prior
            continue
        current = details(db, sku)
        matches = [r for r in current["rows"] if r["contact_id"] == contact_id]
        if len(matches) == 1:
            result[sku] = {"sku": sku, "supplier_version": current["version"], **matches[0]}
    return result
