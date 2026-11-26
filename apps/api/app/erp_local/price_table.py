"""What DaVinci's Tabela de Preços says of each product, and the result of every cost change.

Novos procedimentos 11 (15/11/2026): the stock control holds, beside name, code, cost and
measures, the Tabela de Preços information: department, EAN, model and certificate (Anatel or
Inmetro), warranty, product and packaging photos and the stock priority. While DaVinci keeps that
table it is read from its copy (`reference_rows`, kind `pricing`, one row per product line such as
"dg052,dg053,dg054"); a SKU finds its line by its family code (dg053.sp → dg053).

Every cost change of a simple product is kept with its result: (new cost − previous cost) × the
physical quantity in all depots at that moment (item 5: 10 units from 100 to 120 = +200,00).
"""

import json
import re
import sqlite3
from decimal import Decimal, InvalidOperation

SCHEMA = """
CREATE TABLE IF NOT EXISTS cost_changes (
 id INTEGER PRIMARY KEY AUTOINCREMENT, operation_key TEXT NOT NULL, sku TEXT NOT NULL,
 cost_from TEXT NOT NULL, cost_to TEXT NOT NULL, quantity TEXT NOT NULL, result TEXT NOT NULL,
 actor TEXT NOT NULL, created_at TEXT NOT NULL);
"""
DEPARTMENTS = {"celular": "Celular", "mala": "Mala", "eletro": "Eletro"}


def family(sku):
    return str(sku or "").split("+")[0].split(".")[0].strip().lower()


def _codes(value):
    return [c.strip().lower() for c in str(value or "").split(",") if c.strip()]


def _ean_for(raw, code):
    """The line's EAN field holds one EAN or "dg052: 7908…, dg053: 7908…"."""
    text = str(raw or "").strip()
    if ":" not in text:
        return text
    for part in text.split(","):
        name, _, value = part.partition(":")
        if name.strip().lower() == code:
            return value.strip()
    return ""


def index(db):
    try:
        rows = db.execute("SELECT data FROM reference_rows WHERE kind='pricing'").fetchall()
    except sqlite3.OperationalError:
        return {}
    out = {}
    for row in rows:
        line = json.loads(row[0])
        for code in _codes(line.get("sku")):
            out[code] = line
    return out


# O nome do certificado como a tela mostra (o DaVinci grava em minúsculas).
# Anvisa entrou com a M-68 (26/11/2026), só manual.
CERTIFICATE_NAMES = {
    "anatel": "Anatel",
    "inmetro": "Inmetro",
    "anvisa": "Anvisa",
    "isento": "Isento",
}


def for_sku(lines, sku):
    code = family(sku)
    line = lines.get(code)
    if not line:
        return None
    certificate = str(line.get("certificate") or "").strip().lower()
    return {
        "line": line.get("sku"),
        "line_name": line.get("name"),
        "department": DEPARTMENTS.get(str(line.get("department") or ""), ""),
        "ean": _ean_for(line.get("ean"), code),
        "certificate": CERTIFICATE_NAMES.get(certificate, certificate),
        "certificate_number": line.get("certificate_number") or "",
        "model": line.get("certificate_model") or line.get("model") or "",
        "warranty_months": line.get("warranty_months"),
        "priority": line.get("priority") or "",
        "photos_url": _safe_url(line.get("photos_url")),
        "packaging_url": _safe_url(line.get("packaging_url")),
    }


def _safe_url(value):
    text = str(value or "").strip()
    return text if re.match(r"^https://", text) else ""


def _decimal(value):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def gaps(db):
    """Active simple products the Tabela de Preços does not cover yet (Novos procedimentos 11,
    item 2) or that have no department (13, item 3: every product has a revenue category)."""
    try:
        rows = db.execute(
            "SELECT p.sku,p.name FROM products p JOIN catalog_origin c ON c.sku=p.sku "
            "WHERE p.kind='simple' AND json_extract(c.data,'$.sellable')=1 ORDER BY p.sku"
        ).fetchall()
    except sqlite3.OperationalError:
        return {"active": 0, "no_line": [], "no_department": []}
    lines = index(db)
    no_line, no_department = [], []
    for sku, name in rows:
        found = for_sku(lines, sku)
        if not found:
            no_line.append({"sku": sku, "name": name, "family": family(sku)})
        elif not found["department"]:
            no_department.append({"sku": sku, "name": name, "line": found["line"]})
    return {"active": len(rows), "no_line": no_line, "no_department": no_department}


def record_cost_change(erp, db, key, sku, before, after, actor):
    """Kept only for a simple product whose cost really changed and had a cost before."""
    old, new = _decimal(before), _decimal(after)
    if old is None or new is None or old == new:
        return None
    kind = db.execute("SELECT kind FROM products WHERE sku=?", (sku,)).fetchone()
    if not kind or kind[0] != "simple":
        return None
    db.executescript(SCHEMA)
    quantity = sum(
        (Decimal(r[0]) for r in db.execute("SELECT physical FROM balances WHERE sku=?", (sku,))),
        Decimal(0),
    )
    result = ((new - old) * quantity).quantize(Decimal("0.01"))
    db.execute(
        "INSERT INTO cost_changes(operation_key,sku,cost_from,cost_to,quantity,result,actor,"
        "created_at) VALUES (?,?,?,?,?,?,?,?)",
        (key, sku, str(old), str(new), str(quantity), str(result), actor, erp.now()),
    )
    return {
        "sku": sku,
        "from": str(old),
        "to": str(new),
        "quantity": str(quantity),
        "result": str(result),
    }


def cost_changes(db, limit=500):
    try:
        rows = db.execute(
            "SELECT * FROM cost_changes ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(r) for r in rows]


def cost_changes_csv(db):
    """The summary DaVinci's Importação will read: one line per change, result in reais."""
    lines = ["data;sku;custo_anterior;custo_novo;quantidade;resultado;quem"]
    for r in reversed(cost_changes(db, limit=100000)):
        cells = [
            r["created_at"],
            r["sku"],
            r["cost_from"],
            r["cost_to"],
            r["quantity"],
            r["result"],
            r["actor"],
        ]
        lines.append(";".join(_cell(c) for c in cells))
    return "\n".join(lines) + "\n"


def _cell(value):
    text = str(value).replace(";", ",").replace("\n", " ")
    return "'" + text if text[:1] in "=+-@" and not re.match(r"^-?\d", text) else text


def listings(db, sku):
    """Novos procedimentos 11, item 4: the listings linked to this product in each store, as
    DaVinci keeps them (Bling's "Vínculos com lojas")."""
    try:
        rows = {
            kind: [
                json.loads(r[0])
                for r in db.execute("SELECT data FROM reference_rows WHERE kind=?", (kind,))
            ]
            for kind in ("products", "links", "integrations")
        }
    except sqlite3.OperationalError:
        return []
    wanted = str(sku).strip().lower()
    ids = {p["id"] for p in rows["products"] if str(p.get("sku") or "").strip().lower() == wanted}
    stores = {i["id"]: i for i in rows["integrations"]}
    out = []
    for link in rows["links"]:
        if link.get("product_id") not in ids or link.get("platform") == "bling":
            continue
        store = stores.get(link.get("integration_id")) or {}
        out.append(
            {
                "channel": link.get("platform"),
                "store": store.get("name") or "",
                "archived": bool(store.get("archived_at")),
                "listing": link.get("external_id"),
                "variation": link.get("variation_id") or "",
                "listing_sku": link.get("external_sku") or "",
                "title": link.get("listing_title") or "",
                "stock": link.get("stock"),
                "price": link.get("price"),
                "sync": link.get("last_sync_status") or "",
                "synced_at": link.get("last_sync_at"),
            }
        )
    return sorted(out, key=lambda r: (r["channel"] or "", r["store"], str(r["listing"])))
