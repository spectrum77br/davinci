"""Own stocktaking. Reads persist locally; only closing a count posts a balance.

Any physical movement after opening invalidates that product's count, even if a
later reverse movement restored the same quantity. Reservations are checked at
posting time by the core ledger. Uncounted products are never zeroed implicitly.
"""

import hashlib
import json
from decimal import Decimal
from uuid import uuid4

from app.erp_local.core import ERPError, dumps, number, text

SCHEMA = """
CREATE TABLE IF NOT EXISTS inventory_sessions (
 id TEXT PRIMARY KEY, depot TEXT REFERENCES deposits(id), status TEXT NOT NULL,
 cycle INTEGER NOT NULL, baseline TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS inventory_active_depot ON inventory_sessions(depot)
 WHERE status='counting';
CREATE TABLE IF NOT EXISTS inventory_lines (
 session_id TEXT REFERENCES inventory_sessions(id), sku TEXT REFERENCES products(sku),
 quantity TEXT NOT NULL, PRIMARY KEY(session_id,sku));
CREATE TABLE IF NOT EXISTS inventory_reads (
 seq INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT REFERENCES inventory_sessions(id),
 read_id TEXT NOT NULL, fingerprint TEXT NOT NULL, cycle INTEGER NOT NULL,
 sku TEXT NOT NULL, quantity TEXT NOT NULL, mode TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(session_id,read_id));
"""
FIELDS = {
    "inventory.open": {"depot"},
    "inventory.scan": {"id", "read_id", "sku", "quantity", "identifier_type"},
    "inventory.set": {"id", "read_id", "sku", "quantity", "identifier_type"},
    "inventory.reset": {"id"},
    "inventory.generate": {"id"},
    "inventory.cancel": {"id"},
}
COMMANDS = set(FIELDS)


def physical_marker(db, sku, depot):
    return db.execute(
        "SELECT coalesce(max(id),0) FROM movements WHERE sku=? AND depot=? "
        "AND CAST(physical_delta AS REAL) != 0",
        (sku, depot),
    ).fetchone()[0]


def baseline(erp, db, depot):
    return {
        r["sku"]: {
            "physical": str(erp.balance(db, r["sku"], depot)[0]),
            "marker": physical_marker(db, r["sku"], depot),
        }
        for r in db.execute("SELECT sku FROM products WHERE kind='simple'")
    }


def session(db, identifier):
    row = db.execute(
        "SELECT * FROM inventory_sessions WHERE id=?", (text(identifier, "conferência"),)
    ).fetchone()
    if not row:
        raise ERPError("inventory_not_found", "Conferência não encontrada.", 404)
    return row


def conflicts(erp, db, row):
    original = json.loads(row["baseline"])
    return [
        line["sku"]
        for line in db.execute("SELECT * FROM inventory_lines WHERE session_id=?", (row["id"],))
        if line["sku"] not in original
        or original[line["sku"]]["marker"] != physical_marker(db, line["sku"], row["depot"])
        or Decimal(original[line["sku"]]["physical"])
        != erp.balance(db, line["sku"], row["depot"])[0]
    ]


def apply(erp, db, key, kind, p):
    if not isinstance(p, dict) or set(p) - FIELDS[kind]:
        raise ERPError("inventory_fields", "Campos de conferência não reconhecidos.")
    if kind == "inventory.open":
        depot = erp.depot(db, p.get("depot"))
        from app.erp_local.deposits import require_active

        require_active(db, depot)
        current = db.execute(
            "SELECT id FROM inventory_sessions WHERE depot=? AND status='counting'", (depot,)
        ).fetchone()
        if current:
            return {"id": current["id"], "duplicate": True, "external_writes": False}
        identifier = str(uuid4())
        db.execute(
            "INSERT INTO inventory_sessions VALUES (?,?,'counting',1,?,?)",
            (identifier, depot, dumps(baseline(erp, db, depot)), erp.now()),
        )
        return {"id": identifier, "duplicate": False, "external_writes": False}
    row = session(db, p.get("id"))
    if kind == "inventory.generate" and row["status"] == "posted":
        return {"id": row["id"], "unchanged": True, "external_writes": False}
    if row["status"] != "counting":
        raise ERPError("inventory_closed", "Esta conferência já foi encerrada.", 409)
    if kind == "inventory.cancel":
        db.execute("UPDATE inventory_sessions SET status='cancelled' WHERE id=?", (row["id"],))
        return {"id": row["id"], "status": "cancelled", "external_writes": False}
    if kind == "inventory.reset":
        db.execute("DELETE FROM inventory_lines WHERE session_id=?", (row["id"],))
        db.execute(
            "UPDATE inventory_sessions SET cycle=cycle+1,baseline=? WHERE id=?",
            (dumps(baseline(erp, db, row["depot"])), row["id"]),
        )
        return {"id": row["id"], "cycle": row["cycle"] + 1, "external_writes": False}
    if kind == "inventory.generate":
        lines = db.execute(
            "SELECT * FROM inventory_lines WHERE session_id=? ORDER BY sku", (row["id"],)
        ).fetchall()
        if not lines:
            raise ERPError(
                "inventory_empty", "Conte pelo menos um produto antes de gerar o balanço."
            )
        if conflicts(erp, db, row):
            raise ERPError(
                "inventory_conflict",
                "Houve movimentação física durante a contagem. Reinicie e confira novamente.",
                409,
            )
        for line in lines:
            erp.apply(
                db,
                key,
                "stock.post",
                {
                    "sku": line["sku"],
                    "depot": row["depot"],
                    "type": "B",
                    "quantity": line["quantity"],
                    "reason": "conferência " + row["id"],
                },
            )
        db.execute("UPDATE inventory_sessions SET status='posted' WHERE id=?", (row["id"],))
        return {
            "id": row["id"],
            "products": len(lines),
            "status": "posted",
            "external_writes": False,
        }
    from app.erp_local.product_details import resolve_reading

    (sku, units), read_id = (
        resolve_reading(db, p.get("sku"), identifier_type=p.get("identifier_type", "auto")),
        text(p.get("read_id"), "identificador da leitura"),
    )
    quantity = number(number(p.get("quantity"), zero=kind == "inventory.set") * units, zero=True)
    fingerprint = hashlib.sha256(dumps([kind, sku, str(quantity)]).encode()).hexdigest()
    previous = db.execute(
        "SELECT fingerprint FROM inventory_reads WHERE session_id=? AND read_id=?",
        (row["id"], read_id),
    ).fetchone()
    if previous:
        if previous["fingerprint"] != fingerprint:
            raise ERPError(
                "inventory_read_conflict",
                "Identificador de leitura já usado com outros dados.",
                409,
            )
        return {"id": row["id"], "duplicate": True, "external_writes": False}
    product = erp.product(db, sku)
    if product["kind"] != "simple":
        raise ERPError("inventory_kit", "Conte as peças físicas que compõem o kit virtual.")
    if sku not in json.loads(row["baseline"]):
        raise ERPError(
            "inventory_new_product",
            "Produto criado depois da abertura. Reinicie a conferência.",
            409,
        )
    current = db.execute(
        "SELECT quantity FROM inventory_lines WHERE session_id=? AND sku=?", (row["id"], sku)
    ).fetchone()
    value = quantity + (Decimal(current[0]) if current and kind == "inventory.scan" else 0)
    value = number(value, zero=True)
    db.execute(
        "INSERT INTO inventory_lines VALUES (?,?,?) ON CONFLICT(session_id,sku) "
        "DO UPDATE SET quantity=excluded.quantity",
        (row["id"], sku, str(value)),
    )
    db.execute(
        "INSERT INTO inventory_reads(session_id,read_id,fingerprint,cycle,sku,quantity,mode,"
        "created_at) VALUES (?,?,?,?,?,?,?,?)",
        (
            row["id"],
            read_id,
            fingerprint,
            row["cycle"],
            sku,
            str(quantity),
            "set" if kind == "inventory.set" else "add",
            erp.now(),
        ),
    )
    return {
        "id": row["id"],
        "sku": sku,
        "counted": str(value),
        "units_per_read": str(units),
        "duplicate": False,
        "external_writes": False,
    }


def snapshot(erp, db):
    sessions = []
    for row in db.execute(
        "SELECT * FROM inventory_sessions WHERE status='counting' OR id IN "
        "(SELECT id FROM inventory_sessions ORDER BY rowid DESC LIMIT 20) ORDER BY rowid DESC"
    ):
        lines = []
        changed = set(conflicts(erp, db, row)) if row["status"] == "counting" else set()
        for line in db.execute(
            "SELECT l.*,p.name FROM inventory_lines l JOIN products p ON p.sku=l.sku "
            "WHERE session_id=? ORDER BY l.sku",
            (row["id"],),
        ):
            physical, reserved = erp.balance(db, line["sku"], row["depot"])
            lines.append(
                {
                    "sku": line["sku"],
                    "name": line["name"],
                    "quantity": line["quantity"],
                    "physical": str(physical),
                    "reserved": str(reserved),
                    "difference": str(Decimal(line["quantity"]) - physical),
                    "conflict": line["sku"] in changed,
                }
            )
        reads = [
            dict(r)
            for r in db.execute(
                "SELECT read_id,sku,quantity,mode,cycle,created_at FROM inventory_reads "
                "WHERE session_id=? ORDER BY seq DESC LIMIT 100",
                (row["id"],),
            )
        ]
        sessions.append(
            {
                "id": row["id"],
                "depot": row["depot"],
                "status": row["status"],
                "cycle": row["cycle"],
                "created_at": row["created_at"],
                "lines": lines,
                "reads": reads,
                "counted_total": str(
                    sum((Decimal(line["quantity"]) for line in lines), Decimal(0))
                ),
            }
        )
    return {"sessions": sessions, "external_writes": False}
