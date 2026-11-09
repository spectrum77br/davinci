"""Automatic choice of the stock lot, following the rules DaVinci runs today.

Order of decision: a Flex order must leave from the Flex lot or stop for a person; an order with
several items stays in one lot when one lot covers everything; otherwise each item follows the
priority registered for its product. When nothing qualifies the order stays as it is. Commercial
item, price and quantity never change: only the physical lot of exit does.
"""

import json
from collections import Counter, defaultdict
from decimal import Decimal

from app.erp_local import stock_allocation
from app.erp_local.core import ERPError, text
from app.erp_local.stock_families import SALE_LOTS, lot

SCHEMA = """
CREATE TABLE IF NOT EXISTS stock_priority_rules (
 base TEXT PRIMARY KEY, lot TEXT NOT NULL, updated_at TEXT NOT NULL);
"""
COMMANDS = {"stock.priority.rule", "order.stock_priority"}
FLEX_LOT = "sp"
REASON = "Prioridade de estoque: "


def lot_of(sku):
    """The single lot of a SKU, or None when absent or mixed (those are never moved)."""
    lots = {lot(part.strip().lower()) for part in sku.split("+") if part.strip()} - {None}
    return next(iter(lots)) if len(lots) == 1 else None


def base_of(sku):
    for part in sku.lower().split("+"):
        part = part.strip()
        found = lot(part)
        if found:
            return part[: -(len(found) + 1)]
    return None


def in_lot(sku, current, target):
    pieces = []
    for part in sku.split("+"):
        raw = part.strip()
        pieces.append(raw[: -len(current)] + target if lot(raw.lower()) == current else raw)
    return "+".join(pieces)


def _flex_demand(erp, db, excluding):
    """Units in the Flex lot already promised to Flex orders that have not moved there yet."""
    demand = defaultdict(Decimal)
    for row in db.execute("SELECT id,data FROM documents WHERE kind='order' AND status='reserved'"):
        if row["id"] == excluding:
            continue
        data = json.loads(row["data"])
        if data.get("fulfillment_mode") != "flex":
            continue
        for item in data.get("fulfillment_items", data["items"]):
            current = lot_of(item["sku"])
            if not current or current == FLEX_LOT:
                continue
            target = in_lot(item["sku"], current, FLEX_LOT)
            if not db.execute("SELECT 1 FROM products WHERE sku=?", (target,)).fetchone():
                continue
            parts = erp.requirements(db, [{"sku": target, "quantity": item["quantity"]}])
            for part, quantity in parts.items():
                demand[part] += Decimal(quantity)
    return demand


def _capacity(erp, db, sku, order, status, reserved_for_flex):
    """Units of this SKU this order could take, counting what it already holds."""
    held = order["requirements"] if status == "reserved" else {}
    needs = erp.requirements(db, [{"sku": sku, "quantity": "1"}])
    if not needs:
        return None
    values = []
    for part, quantity in needs.items():
        origin = erp.origin(db, part)
        if origin and (origin["stock_state"] != "known" or origin["catalog_state"] != "ready"):
            return None
        physical, reserved = erp.balance(db, part, order["depot"])
        free = physical - reserved + Decimal(held.get(part, "0")) - reserved_for_flex[part]
        values.append(free / Decimal(quantity))
    return min(values)


def _active_sku(erp, db, sku):
    from app.erp_local.product_details import details

    row = db.execute("SELECT 1 FROM products WHERE sku=?", (sku,)).fetchone()
    return bool(row) and details(db, sku)["status"] == "active"


def plan(erp, db, identifier):
    """Decide without changing anything. Returns decision, reason and the lot per item."""
    document, order = erp.document(db, identifier, "order")
    stock_allocation.guard(db, document, order)
    flex = order.get("fulfillment_mode") == "flex"
    chosen = order.get("fulfillment_items", order["items"])
    rules = {r["base"]: r["lot"] for r in db.execute("SELECT * FROM stock_priority_rules")}
    reserved = defaultdict(Decimal) if flex else _flex_demand(erp, db, identifier)
    lines = []
    for index, item in enumerate(chosen):
        sku = item["sku"]
        lines.append(
            {
                "index": index,
                "sku": sku,
                "lot": lot_of(sku),
                "base": base_of(sku),
                "quantity": Decimal(item["quantity"]),
            }
        )

    def coverage(line, target_lot):
        if not line["lot"]:
            return None
        target = (
            line["sku"]
            if target_lot == line["lot"]
            else in_lot(line["sku"], line["lot"], target_lot)
        )
        if not _active_sku(erp, db, target):
            return None
        try:
            if not stock_allocation.compatible(erp, db, line["sku"], target):
                return None
            capacity = _capacity(erp, db, target, order, document["status"], reserved)
        except ERPError:
            return None
        if capacity is None or capacity < line["quantity"]:
            return None
        return target, capacity

    def result(decision, why, targets=None):
        moves = [
            {"index": line["index"], "from": line["sku"], "to": targets[line["index"]]}
            for line in lines
            if targets and targets[line["index"]] != line["sku"]
        ]
        return {
            "id": document["id"],
            "decision": "move"
            if decision == "move" and moves
            else decision
            if not moves
            else "move",
            "reason": why,
            "moves": moves,
            "items": [
                {"index": line["index"], "sku": (targets or {}).get(line["index"], line["sku"])}
                for line in lines
            ],
            "version": order.get("version", 0),
        }

    if flex:
        targets = {}
        for line in lines:
            found = coverage(line, FLEX_LOT)
            if not found:
                return {**result("alert", "flex_sem_estoque"), "alert": True}
            targets[line["index"]] = found[0]
        return result("move", "flex", targets)

    sale_lines = [line for line in lines if line["lot"] in SALE_LOTS]
    if len(sale_lines) >= 2 and len(sale_lines) == len(lines):
        votes = Counter(rules[line["base"]] for line in lines if line["base"] in rules)
        present = {line["lot"] for line in lines}
        options = {}
        for candidate in sorted(present | set(votes)):
            found = [coverage(line, candidate) for line in lines]
            if all(found):
                options[candidate] = {
                    "targets": {line["index"]: f[0] for line, f in zip(lines, found, strict=True)},
                    "total": sum((f[1] for f in found), Decimal(0)),
                    "swaps": sum(
                        1 for line, f in zip(lines, found, strict=True) if f[0] != line["sku"]
                    ),
                }
        current = next(iter(present)) if len(present) == 1 else None

        def best(names):
            if current in names:
                return current
            return max(sorted(names), key=lambda n: (options[n]["total"], -options[n]["swaps"]))

        for count in sorted(set(votes.values()), reverse=True):
            names = [n for n, v in votes.items() if v == count and n in options]
            if names:
                pick = best(names)
                return _guarded(
                    db,
                    result("move", "estoque_unico_prioridade", options[pick]["targets"]),
                    lines,
                    coverage,
                )
        if current in options:
            return result("keep", "ja_no_lote_certo")
        if options:
            pick = best(list(options))
            return _guarded(
                db,
                result("move", "estoque_unico_mais_saldo", options[pick]["targets"]),
                lines,
                coverage,
            )

    targets = {}
    for line in lines:
        target_lot = rules.get(line["base"]) if line["base"] else None
        targets[line["index"]] = line["sku"]
        if target_lot and line["lot"] and target_lot != line["lot"]:
            found = coverage(line, target_lot)
            if found:
                targets[line["index"]] = found[0]
    return _guarded(db, result("move", "prioridade", targets), lines, coverage)


def _guarded(db, decision, lines, coverage):
    """A swap already made by this robot is not undone unless the current lot stopped covering."""
    if not decision["moves"]:
        return {**decision, "decision": "keep"}
    swapped_before = db.execute(
        "SELECT data FROM stock_allocation_history WHERE order_id=?", (decision["id"],)
    ).fetchall()
    if not any(json.loads(r[0]).get("reason", "").startswith(REASON) for r in swapped_before):
        return decision
    by_index = {line["index"]: line for line in lines}
    needed = any(
        not coverage(by_index[move["index"]], by_index[move["index"]]["lot"])
        for move in decision["moves"]
    )
    if needed:
        return decision
    return {**decision, "decision": "keep", "reason": "trava_troca_repetida", "moves": []}


def _save_rule(erp, db, p):
    if not isinstance(p, dict) or set(p) != {"base", "lot"}:
        raise ERPError("priority_fields", "Informe produto base e estoque prioritário.")
    base = text(p["base"], "produto base").lower()
    if p["lot"] in (None, ""):
        db.execute("DELETE FROM stock_priority_rules WHERE base=?", (base,))
        return {"base": base, "lot": None, "external_writes": False}
    if p["lot"] not in SALE_LOTS:
        raise ERPError("priority_lot", "Escolha um estoque de venda.")
    db.execute(
        "INSERT INTO stock_priority_rules VALUES (?,?,?) ON CONFLICT(base) DO UPDATE SET "
        "lot=excluded.lot,updated_at=excluded.updated_at",
        (base, p["lot"], erp.now()),
    )
    return {"base": base, "lot": p["lot"], "external_writes": False}


def apply(erp, db, key, kind, p):
    if kind == "stock.priority.rule":
        return _save_rule(erp, db, p)
    if not isinstance(p, dict) or set(p) != {"id"}:
        raise ERPError("priority_fields", "Informe o pedido.")
    decision = plan(erp, db, p["id"])
    if decision["decision"] == "move":
        stock_allocation.apply(
            erp,
            db,
            key,
            {
                "id": decision["id"],
                "version": decision["version"],
                "items": decision["items"],
                "reason": REASON + decision["reason"],
            },
        )
    return {
        "id": decision["id"],
        "decision": decision["decision"],
        "reason": decision["reason"],
        "moves": decision["moves"],
        "alert": bool(decision.get("alert")),
        "external_writes": False,
    }


def sweep(erp, *, new_key):
    """One round, Flex first, so a normal order never takes the last unit a Flex one needs."""
    with erp.connection() as db:
        rows = db.execute(
            "SELECT id,data FROM documents WHERE kind='order' AND status='reserved'"
        ).fetchall()
    ordered = sorted(
        rows,
        key=lambda r: (
            json.loads(r["data"]).get("fulfillment_mode") != "flex",
            json.loads(r["data"]).get("created_at", ""),
        ),
    )
    report = {"seen": len(ordered), "moved": 0, "alerts": [], "skipped": 0}
    for row in ordered:
        try:
            result = erp.command(new_key(), "order.stock_priority", {"id": row["id"]})
        except ERPError:
            report["skipped"] += 1
            continue
        report["moved"] += result["decision"] == "move"
        if result["alert"]:
            report["alerts"].append(row["id"])
    return report


def snapshot(db):
    return [dict(r) for r in db.execute("SELECT * FROM stock_priority_rules ORDER BY base")]
