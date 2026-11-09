"""Explicit physical lot choice, preserving the original commercial order.

Derived from the existing Davinci family rule. This is an operator-reviewed
allocation, not the production priority/Flex robot and not pooled publication.
Kits are checked against their actual expanded recipes, not just SKU spelling.
"""

import json
from collections import defaultdict
from decimal import ROUND_FLOOR, Decimal

from app.erp_local.core import ERPError, dumps, text
from app.erp_local.stock_families import key as family_key

SCHEMA = """
CREATE TABLE IF NOT EXISTS stock_allocation_history (
 order_id TEXT REFERENCES documents(id), version INTEGER NOT NULL,
 operation_key TEXT REFERENCES events(operation_key), data TEXT NOT NULL,
 PRIMARY KEY(order_id,version));
"""
COMMANDS = {"order.allocate_stock"}


def signature(erp, db, sku):
    product = erp.product(db, sku)
    parts = (
        json.loads(product["components"])
        if product["kind"] == "kit"
        else [{"sku": sku, "quantity": "1"}]
    )
    result = defaultdict(Decimal)
    for part in parts:
        result[family_key(part["sku"]) or "sku:" + part["sku"].lower()] += Decimal(part["quantity"])
    return product["kind"], dict(result)


def compatible(erp, db, original, target):
    if original == target:
        return True
    family = family_key(original)
    return bool(
        family
        and family == family_key(target)
        and signature(erp, db, original) == signature(erp, db, target)
    )


def guard(db, document, order):
    if (
        document["status"] not in {"draft", "reserved"}
        or order.get("stock_posted")
        or order.get("pos")
    ):
        raise ERPError("allocation_stage", "Escolha o lote antes da saída de estoque.", 409)
    if db.execute("SELECT 1 FROM logistics_shipments WHERE id=?", (document["id"],)).fetchone():
        raise ERPError("allocation_shipping", "Concilie a separação antes de trocar o lote.", 409)
    if db.execute(
        "SELECT 1 FROM accounts WHERE document_id=? AND kind='receivable' AND status!='cancelled'",
        (document["id"],),
    ).fetchone():
        raise ERPError("allocation_accounts", "Estorne as contas antes de trocar o lote.", 409)


def options(erp, db, identifier):
    document, order = erp.document(db, identifier, "order")
    guard(db, document, order)
    products = [r[0] for r in db.execute("SELECT sku FROM products ORDER BY sku")]
    chosen = order.get("fulfillment_items", order["items"])
    held = order["requirements"] if document["status"] == "reserved" else {}
    result = []
    for index, original in enumerate(order["items"]):
        family = family_key(original["sku"])
        candidates = [
            s for s in products if s == original["sku"] or family and family_key(s) == family
        ]
        values = []
        for sku in candidates:
            if not compatible(erp, db, original["sku"], sku):
                continue
            capacity, reason = None, None
            try:
                erp.items(db, [{**original, "sku": sku}])
                requirements = erp.requirements(db, [{"sku": sku, "quantity": "1"}])
                capacities = []
                for part, quantity in requirements.items():
                    origin = erp.origin(db, part)
                    if origin and (
                        origin["stock_state"] != "known" or origin["catalog_state"] != "ready"
                    ):
                        raise ERPError("stock_review", "Saldo a conferir.")
                    physical, reserved = erp.balance(db, part, order["depot"])
                    capacities.append(
                        (physical - reserved + Decimal(held.get(part, "0"))) / Decimal(quantity)
                    )
                capacity = str(
                    max(Decimal(0), min(capacities)).quantize(
                        Decimal("0.001"), rounding=ROUND_FLOOR
                    )
                )
            except ERPError as exc:
                reason = exc.code
            values.append({"sku": sku, "capacity_including_this_order": capacity, "review": reason})
        result.append(
            {
                "index": index,
                "commercial_sku": original["sku"],
                "quantity": original["quantity"],
                "selected_sku": chosen[index]["sku"],
                "options": values,
            }
        )
    return {
        "id": document["id"],
        "version": order.get("version", 0),
        "depot": order["depot"],
        "items": result,
        "history": history(db, document["id"]),
        "external_writes": False,
    }


def history(db, identifier):
    return [
        json.loads(r[0])
        for r in db.execute(
            "SELECT data FROM stock_allocation_history WHERE order_id=? ORDER BY version DESC",
            (identifier,),
        )
    ]


def apply(erp, db, operation_key, payload):
    from app.erp_local.product_details import document_snapshot

    if not isinstance(payload, dict) or set(payload) != {"id", "version", "items", "reason"}:
        raise ERPError("allocation_fields", "Informe pedido, versão, lotes e motivo.")
    document, order = erp.document(db, payload["id"], "order")
    guard(db, document, order)
    version = order.get("version", 0)
    if type(payload["version"]) is not int or payload["version"] != version:
        raise ERPError("allocation_version", "O pedido mudou. Atualize a escolha de lote.", 409)
    reason = text(payload["reason"], "motivo da escolha de lote", 300)
    raw = payload["items"]
    if not isinstance(raw, list) or len(raw) != len(order["items"]):
        raise ERPError("allocation_items", "Confira o lote de todos os itens.")
    fulfillment = []
    for index, (choice, original) in enumerate(zip(raw, order["items"], strict=True)):
        if (
            not isinstance(choice, dict)
            or set(choice) != {"index", "sku"}
            or type(choice["index"]) is not int
            or choice["index"] != index
        ):
            raise ERPError("allocation_items", "Confira a identificação dos itens.")
        target = text(choice["sku"], "SKU de saída")
        erp.product(db, target)
        if not compatible(erp, db, original["sku"], target):
            raise ERPError(
                "allocation_family", "O lote escolhido difere do produto ou da composição.", 409
            )
        fulfillment.append({**original, "sku": target})
    fulfillment = erp.items(db, fulfillment)
    requirements = erp.requirements(db, fulfillment)
    if (
        fulfillment == order.get("fulfillment_items", order["items"])
        and requirements == order["requirements"]
    ):
        return {
            "id": document["id"],
            "version": version,
            "unchanged": True,
            "external_writes": False,
        }
    if document["status"] == "reserved":
        for sku in sorted(set(order["requirements"]) | set(requirements)):
            delta = Decimal(requirements.get(sku, "0")) - Decimal(
                order["requirements"].get(sku, "0")
            )
            if delta:
                erp.move(
                    db,
                    operation_key,
                    sku,
                    order["depot"],
                    Decimal(0),
                    delta,
                    "escolha do lote de saída",
                )
    before = order.get("fulfillment_items", order["items"])
    fulfillment_snapshots = {
        item["sku"]: document_snapshot(erp, db, item["sku"]) for item in fulfillment
    }
    fiscal_review = any(
        not order.get("product_snapshots", {})
        .get(original["sku"], {})
        .get("tax", {})
        .get("version")
        or not fulfillment_snapshots[target["sku"]]["tax"]["version"]
        or order.get("product_snapshots", {}).get(original["sku"], {}).get("tax", {}).get("data")
        != fulfillment_snapshots[target["sku"]]["tax"]["data"]
        for original, target in zip(order["items"], fulfillment, strict=True)
    )
    data = {
        "version": version + 1,
        "reason": reason,
        "at": erp.now(),
        "commercial_items": order["items"],
        "previous_fulfillment": before,
        "fulfillment_items": fulfillment,
        "fulfillment_product_snapshots": fulfillment_snapshots,
        "fiscal_allocation_review": fiscal_review,
        "requirements": requirements,
    }
    db.execute(
        "INSERT INTO stock_allocation_history VALUES (?,?,?,?)",
        (document["id"], version + 1, operation_key, dumps(data)),
    )
    order.update(version=version + 1, requirements=requirements)
    if fulfillment == order["items"]:
        order.pop("fulfillment_items", None)
        order.pop("stock_allocation", None)
        order.pop("fulfillment_product_snapshots", None)
        order.pop("fiscal_allocation_review", None)
    else:
        order.update(
            fulfillment_items=fulfillment,
            fulfillment_product_snapshots=fulfillment_snapshots,
            fiscal_allocation_review=fiscal_review,
            stock_allocation={"version": version + 1, "reason": reason},
        )
    erp.update_document(db, document["id"], document["status"], order)
    return {
        "id": document["id"],
        "version": version + 1,
        "requirements": requirements,
        "external_writes": False,
    }
