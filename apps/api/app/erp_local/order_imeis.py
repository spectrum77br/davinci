"""The IMEI of each phone in an order (Novos procedimentos 2/6, 15/11/2026).

The shipping video reads the IMEI and the order keeps it, so a phone can be traced in warranty and
returns. Each unit holds one or two IMEIs (dual SIM); every IMEI is 15 digits with a valid check
digit and belongs to one order only: the same IMEI in a second order is refused, naming the first.
Only items whose product the Tabela de Preços puts in Celular take IMEIs (a line without a
department is accepted, so a phone missing from the table is not blocked).
"""

import json
import sqlite3

from app.erp_local.core import ERPError, text

COMMANDS = {"order.imei.save"}
SCHEMA = """
CREATE TABLE IF NOT EXISTS order_imeis (
 imei TEXT PRIMARY KEY, order_id TEXT NOT NULL, sku TEXT NOT NULL, unit INTEGER NOT NULL,
 slot INTEGER NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS order_imeis_order ON order_imeis(order_id, sku);
"""


def valid(imei):
    digits = str(imei or "").strip()
    if len(digits) != 15 or not digits.isascii() or not digits.isdigit():
        return False
    total = 0
    for position, char in enumerate(reversed(digits)):
        value = int(char)
        if position % 2:
            value *= 2
            value = value - 9 if value > 9 else value
        total += value
    return total % 10 == 0


def _quantity(order, sku):
    total = 0
    for item in order.get("items") or []:
        if str(item.get("sku")) == sku:
            total += int(float(item.get("quantity") or 0))
    return total


def apply(erp, db, key, p):
    if not isinstance(p, dict) or set(p) != {"id", "sku", "units"}:
        raise ERPError("order_imei", "Informe o pedido, o produto e os IMEIs de cada aparelho.")
    doc, order = erp.document(db, p["id"], "order")
    sku = text(p["sku"], "produto")
    quantity = _quantity(order, sku)
    if not quantity:
        raise ERPError("order_imei", "Este produto não está no pedido.", 404)
    from app.erp_local import price_table

    line = price_table.for_sku(price_table.index(db), sku)
    if line and line["department"] and line["department"] != "Celular":
        raise ERPError(
            "order_imei", "IMEI só vale para celular; este produto é " + line["department"] + "."
        )
    units = p["units"]
    if not isinstance(units, list) or len(units) > quantity:
        raise ERPError("order_imei", f"O pedido tem {quantity} unidade(s) deste produto.")
    rows, seen = [], set()
    for unit, imeis in enumerate(units, 1):
        if not isinstance(imeis, list) or not 1 <= len(imeis) <= 2:
            raise ERPError("order_imei", "Cada aparelho tem um ou dois IMEIs (dual SIM).")
        for slot, imei in enumerate(imeis, 1):
            value = str(imei or "").strip()
            if not valid(value):
                raise ERPError(
                    "order_imei_invalid",
                    f"IMEI {value or 'vazio'} inválido: confira os 15 dígitos.",
                )
            if value in seen:
                raise ERPError("order_imei_repeated", f"O IMEI {value} foi digitado duas vezes.")
            seen.add(value)
            rows.append((value, unit, slot))
    db.executescript(SCHEMA)
    for value, _, _ in rows:
        other = db.execute(
            "SELECT order_id FROM order_imeis WHERE imei=? AND order_id<>?", (value, doc["id"])
        ).fetchone()
        if other:
            number = _order_number(db, other[0])
            raise ERPError("order_imei_used", f"O IMEI {value} já está no pedido {number}.", 409)
    actor = db.execute("SELECT actor FROM events WHERE operation_key=?", (key,)).fetchone()
    db.execute("DELETE FROM order_imeis WHERE order_id=? AND sku=?", (doc["id"], sku))
    for value, unit, slot in rows:
        db.execute(
            "INSERT INTO order_imeis VALUES (?,?,?,?,?,?,?)",
            (value, doc["id"], sku, unit, slot, actor[0] if actor else "ERP", erp.now()),
        )
    return {"id": doc["id"], "sku": sku, "imeis": len(rows), "external_writes": False}


def _order_number(db, order_id):
    row = db.execute("SELECT data FROM documents WHERE id=?", (order_id,)).fetchone()
    return json.loads(row[0]).get("external_id") or order_id if row else order_id


def snapshot(db):
    """{order_id: {sku: [[imei, imei?], ...]}} for the screen."""
    try:
        rows = db.execute("SELECT * FROM order_imeis ORDER BY order_id,sku,unit,slot").fetchall()
    except sqlite3.OperationalError:
        return {}
    out = {}
    for r in rows:
        units = out.setdefault(r["order_id"], {}).setdefault(r["sku"], [])
        while len(units) < r["unit"]:
            units.append([])
        units[r["unit"] - 1].append(r["imei"])
    return out
