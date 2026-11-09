"""Own physical assembly orders, partial deliveries and explicit reversals.

Virtual kits remain derived stock. A physical finished SKU must be a separate
simple product. This module neither emits fiscal documents nor posts accounts.
"""

import json
from decimal import Decimal
from uuid import uuid4

from app.erp_local import procurement, product_details
from app.erp_local.core import ERPError, dumps, number, text
from app.erp_local.finance import day

SCHEMA = """
CREATE TABLE IF NOT EXISTS production_receipts (
 id TEXT PRIMARY KEY, document_id TEXT REFERENCES documents(id), external_key TEXT UNIQUE,
 status TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL);
"""
FIELDS = {
    "production.save": {
        "id",
        "version",
        "reference",
        "sku",
        "quantity",
        "components",
        "origin",
        "destination",
        "responsible",
        "start_on",
        "finish_on",
        "notes",
        "structure_version",
    },
    "production.start": {"id"},
    "production.pause": {"id"},
    "production.cancel": {"id"},
    "production.deliver": {"id", "receipt_id", "quantity", "extra_cost", "notes"},
    "production.reverse": {"id"},
}
COMMANDS = set(FIELDS)


def physical(erp, db, sku):
    product = erp.product(db, sku)
    if product["kind"] != "simple":
        raise ERPError(
            "production_physical", "Selecione um produto físico; kit virtual não é montado."
        )
    origin = erp.origin(db, sku)
    if origin and (origin["catalog_state"] != "ready" or origin["stock_state"] != "known"):
        raise ERPError(
            "production_review", "Concilie identidade e saldo antes de produzir: " + sku, 409
        )
    if product_details.details(db, sku)["status"] != "active":
        raise ERPError("production_inactive", "Produto inativo para nova produção: " + sku, 409)
    return sku


def requirements(data, quantity):
    result = {}
    for item in data["components"]:
        value = number(Decimal(item["quantity"]) * quantity)
        result[item["sku"]] = value
    return result


def reserve(erp, db, key, data, quantity, reason):
    if not quantity:
        return
    for sku, value in requirements(data, abs(quantity)).items():
        erp.move(
            db, key, sku, data["origin"], Decimal(0), value if quantity > 0 else -value, reason
        )


def save(erp, db, p):
    doc, old = erp.document(db, p["id"], "production") if p.get("id") else (None, {})
    if doc and (doc["status"] != "planned" or Decimal(old["completed"])):
        raise ERPError(
            "production_edit", "Somente ordens planejadas sem entregas podem ser editadas.", 409
        )
    version = old.get("version", 0)
    if type(p.get("version", 0)) is not int or p.get("version", 0) != version:
        raise ERPError("production_version", "A ordem mudou. Atualize antes de salvar.", 409)
    sku = physical(erp, db, text(p.get("sku"), "produto"))
    quantity = number(p.get("quantity"))
    parts = p.get("components")
    if not isinstance(parts, list) or not 1 <= len(parts) <= 200:
        raise ERPError(
            "production_components", "Informe a composição física por unidade produzida."
        )
    components = []
    for item in parts:
        if not isinstance(item, dict) or set(item) != {"sku", "quantity"}:
            raise ERPError("production_components", "Componente inválido.")
        part = physical(erp, db, text(item["sku"], "componente"))
        if part == sku or any(i["sku"] == part for i in components):
            raise ERPError(
                "production_cycle", "A composição repete um item ou contém o próprio produto."
            )
        components.append({"sku": part, "quantity": str(number(item["quantity"]))})
    structure = None
    if "structure_version" in p:
        from app.erp_local import product_structures

        structure = product_structures.details(db, erp.product(db, sku))
        if (
            type(p["structure_version"]) is not int
            or p["structure_version"] != structure["version"]
            or not structure["expanded"]
            or sorted(components, key=lambda i: i["sku"]) != structure["expanded"]
        ):
            raise ERPError(
                "production_structure",
                "A estrutura mudou ou os insumos diferem. "
                "Reaplique a composição do cadastro antes de salvar.",
                409,
            )
    reference = text(p.get("reference"), "número da ordem")
    external = dumps(["production", reference])
    duplicate = db.execute("SELECT id FROM documents WHERE external_key=?", (external,)).fetchone()
    if duplicate and (not doc or duplicate[0] != doc["id"]):
        raise ERPError("production_duplicate", "Número da ordem já cadastrado.", 409)
    origin, destination = (erp.depot(db, p.get(k)) for k in ("origin", "destination"))
    dates = {k: day(p[k]) if p.get(k) else "" for k in ("start_on", "finish_on")}
    if dates["start_on"] and dates["finish_on"] and dates["start_on"] > dates["finish_on"]:
        raise ERPError("production_dates", "A previsão final deve ser posterior ao início.")
    data = {
        "reference": reference,
        "sku": sku,
        "quantity": str(quantity),
        "components": components,
        "structure_snapshot": structure,
        "origin": origin,
        "destination": destination,
        **dates,
        "responsible": text(p["responsible"], "responsável") if p.get("responsible") else "",
        "notes": text(p["notes"], "observações", 2000) if p.get("notes") else "",
        "completed": "0.000",
        "reserved_quantity": "0.000",
        "version": version + 1,
        "created_at": old.get("created_at", erp.now()),
        "product_snapshots": {
            s: product_details.document_snapshot(erp, db, s)
            for s in [sku, *[i["sku"] for i in components]]
        },
    }
    requirements(data, quantity)  # Validate multiplied quantity/precision before any effects.
    identifier = doc["id"] if doc else str(uuid4())
    if doc:
        db.execute(
            "UPDATE documents SET external_key=?,data=? WHERE id=?",
            (external, dumps(data), identifier),
        )
    else:
        db.execute(
            "INSERT INTO documents VALUES (?,?,?,?,?)",
            (identifier, "production", external, "planned", dumps(data)),
        )
    return {"id": identifier, "status": "planned", "version": version + 1, "external_writes": False}


def apply(erp, db, key, kind, p):
    if not isinstance(p, dict) or set(p) - FIELDS[kind]:
        raise ERPError("production_fields", "Campos da ordem de produção inválidos.")
    if kind == "production.save":
        return save(erp, db, p)
    if kind == "production.reverse":
        return reverse(erp, db, key, p)
    doc, data = erp.document(db, p.get("id"), "production")
    status = doc["status"]
    if kind == "production.start":
        if status == "running":
            return {"id": doc["id"], "unchanged": True}
        if status not in {"planned", "paused"}:
            raise ERPError("production_transition", "Esta ordem não pode ser iniciada.", 409)
        remaining = Decimal(data["quantity"]) - Decimal(data["completed"])
        to_reserve = remaining - Decimal(data["reserved_quantity"])
        reserve(erp, db, key, data, to_reserve, "reserva produção " + data["reference"])
        data["reserved_quantity"] = str(remaining)
        status = "running"
    elif kind == "production.pause":
        if status not in {"running", "paused"}:
            raise ERPError(
                "production_transition", "Somente uma ordem em produção pode ser pausada.", 409
            )
        status = "paused"
    elif kind == "production.cancel":
        if status == "cancelled":
            return {"id": doc["id"], "unchanged": True}
        if status == "finished":
            raise ERPError(
                "production_transition",
                "Estorne a entrega antes de cancelar a ordem concluída.",
                409,
            )
        reserve(
            erp,
            db,
            key,
            data,
            -Decimal(data["reserved_quantity"]),
            "libera produção " + data["reference"],
        )
        data["reserved_quantity"] = "0.000"
        status = "cancelled"
    elif kind == "production.deliver":
        return deliver(erp, db, key, doc, data, p)
    data["version"] += 1
    erp.update_document(db, doc["id"], status, data)
    return {"id": doc["id"], "status": status, "external_writes": False}


def deliver(erp, db, key, doc, data, p):
    receipt_id = text(p.get("receipt_id"), "identificador da entrega")
    quantity = number(p.get("quantity"))
    extra = number(p.get("extra_cost", "0"), money=True, zero=True)
    notes = text(p["notes"], "observações", 2000) if p.get("notes") else ""
    fingerprint = dumps({"quantity": str(quantity), "extra_cost": str(extra), "notes": notes})
    external = dumps([doc["id"], receipt_id])
    previous = db.execute(
        "SELECT * FROM production_receipts WHERE external_key=?", (external,)
    ).fetchone()
    if previous:
        if json.loads(previous["data"])["input"] != fingerprint:
            raise ERPError(
                "production_receipt_conflict", "Entrega já registrada com outros dados.", 409
            )
        return {"id": previous["id"], "duplicate": True, "status": previous["status"]}
    if doc["status"] != "running":
        raise ERPError("production_transition", "Inicie a produção antes de entregar.", 409)
    remaining = Decimal(data["quantity"]) - Decimal(data["completed"])
    if quantity > remaining or quantity > Decimal(data["reserved_quantity"]):
        raise ERPError("production_quantity", "Entrega ultrapassa a quantidade restante.", 409)
    inputs, value = [], extra
    for sku, consumed in requirements(data, quantity).items():
        before = procurement.pool(erp, db, sku, data["origin"])
        cost = procurement.average(erp, db, sku, data["origin"])
        erp.move(
            db,
            key,
            sku,
            data["origin"],
            -consumed,
            -consumed,
            "consumo produção " + data["reference"],
            cost if cost is not None else "unknown",
        )
        after = procurement.pool(erp, db, sku, data["origin"])
        consumed_value = before - after if before is not None and after is not None else None
        value = value + consumed_value if value is not None and consumed_value is not None else None
        inputs.append(
            {
                "sku": sku,
                "quantity": str(consumed),
                "cost": str(consumed_value / consumed) if consumed_value is not None else "unknown",
                "value": str(consumed_value) if consumed_value is not None else None,
            }
        )
    output_cost = value / quantity if value is not None else "unknown"
    erp.move(
        db,
        key,
        data["sku"],
        data["destination"],
        quantity,
        Decimal(0),
        "entrega produção " + data["reference"],
        output_cost,
    )
    identifier = str(uuid4())
    receipt = {
        "reference": receipt_id,
        "quantity": str(quantity),
        "extra_cost": str(extra),
        "notes": notes,
        "inputs": inputs,
        "output_cost": str(output_cost),
        "output_value": str(value) if value is not None else None,
        "input": fingerprint,
    }
    db.execute(
        "INSERT INTO production_receipts VALUES (?,?,?,?,?,?)",
        (identifier, doc["id"], external, "posted", dumps(receipt), erp.now()),
    )
    data["completed"] = str(Decimal(data["completed"]) + quantity)
    data["reserved_quantity"] = str(Decimal(data["reserved_quantity"]) - quantity)
    data["version"] += 1
    status = "finished" if Decimal(data["completed"]) == Decimal(data["quantity"]) else "running"
    erp.update_document(db, doc["id"], status, data)
    return {
        "id": identifier,
        "production_id": doc["id"],
        "status": status,
        "external_writes": False,
    }


def reverse(erp, db, key, p):
    row = db.execute("SELECT * FROM production_receipts WHERE id=?", (p.get("id"),)).fetchone()
    if not row:
        raise ERPError("production_receipt_not_found", "Entrega de produção não encontrada.", 404)
    if row["status"] == "reversed":
        return {"id": row["id"], "unchanged": True}
    last = db.execute(
        "SELECT id FROM production_receipts WHERE document_id=? AND status='posted' "
        "ORDER BY rowid DESC LIMIT 1",
        (row["document_id"],),
    ).fetchone()
    if last[0] != row["id"]:
        raise ERPError("production_reverse_order", "Estorne primeiro a entrega mais recente.", 409)
    doc, data = erp.document(db, row["document_id"], "production")
    receipt = json.loads(row["data"])
    quantity = Decimal(receipt["quantity"])
    # Reopen as planned: release outstanding reservations explicitly. Starting
    # again revalidates the entire remaining BOM against current availability.
    reserve(
        erp, db, key, data, -Decimal(data["reserved_quantity"]), "reinício após estorno produção"
    )
    erp.move(
        db,
        key,
        data["sku"],
        data["destination"],
        -quantity,
        Decimal(0),
        "estorno entrega produção " + data["reference"],
        receipt["output_cost"],
    )
    for item in receipt["inputs"]:
        erp.move(
            db,
            key,
            item["sku"],
            data["origin"],
            Decimal(item["quantity"]),
            Decimal(0),
            "estorno consumo produção " + data["reference"],
            item["cost"],
        )
    data["completed"] = str(Decimal(data["completed"]) - quantity)
    data["reserved_quantity"] = "0.000"
    data["version"] += 1
    status = "cancelled" if doc["status"] == "cancelled" else "planned"
    erp.update_document(db, doc["id"], status, data)
    db.execute("UPDATE production_receipts SET status='reversed' WHERE id=?", (row["id"],))
    return {
        "id": row["id"],
        "status": "reversed",
        "production_status": status,
        "external_writes": False,
    }


def snapshot(db):
    return {
        "orders": [
            {**dict(r), "data": json.loads(r["data"])}
            for r in db.execute(
                "SELECT * FROM documents WHERE kind='production' ORDER BY rowid DESC"
            )
        ],
        "receipts": [
            {**dict(r), "data": json.loads(r["data"])}
            for r in db.execute("SELECT * FROM production_receipts ORDER BY rowid DESC")
        ],
        "external_writes": False,
    }
