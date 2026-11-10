"""Daily proof that the ERP's stock logic follows Bling's, before the ERP owns any store.

Every hour the worker keeps Bling's available balance per product, as DaVinci mirrors it. Once a
day the change in Bling's available balance is set against what the sales read from the stores
explain: a paid sale takes its units, kits through their parts. Available is the measure because
it moves at payment in both systems, whatever moment each one takes the units out physically.
Purchases, returns, manual adjustments and channels the ERP does not read show as differences,
listed by product family so a person can see why.
"""

import json
from collections import defaultdict
from decimal import Decimal, InvalidOperation

from app.erp_local import native_ingest, stock_families

SCHEMA = """
CREATE TABLE IF NOT EXISTS stock_snapshots (taken_at TEXT PRIMARY KEY, data TEXT NOT NULL);
"""
UNPAID = {
    "UNPAID",
    "PAYMENT_REQUIRED",
    "PAYMENT_IN_PROCESS",
    "PENDING",
    "AWAITING_PAYMENT",
    "PENDINGAVAILABILITY",
    "PENDING_AVAILABILITY",
}
KEEP_DAYS = 4


def _number(value):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number.is_finite() else None


def take(erp, products, now=None):
    """Keep Bling's available balance per Bling product id, as the reference reports it."""
    data = {}
    for row in products:
        available = _number(row.get("stock"))
        if row.get("bling_product_id") and available is not None:
            data[str(row["bling_product_id"])] = str(available)
    if not data:
        return None
    taken = now or erp.now()
    with erp.connection() as db:
        db.executescript(SCHEMA)
        db.execute("INSERT OR REPLACE INTO stock_snapshots VALUES (?,?)", (taken, json.dumps(data)))
        db.execute(
            "DELETE FROM stock_snapshots WHERE taken_at < datetime(?, ?)",
            (taken, f"-{KEEP_DAYS} days"),
        )
    return taken


def _kits(db):
    return {
        r["sku"]: (r["kind"], json.loads(r["components"] or "[]"))
        for r in db.execute("SELECT sku,kind,components FROM products")
    }


def _expand(kits, sku, quantity, out, depth=0):
    kind, parts = kits.get(sku, ("simple", []))
    if kind == "kit" and parts and depth < 4:
        for part in parts:
            _expand(kits, part["sku"], quantity * Decimal(str(part["quantity"])), out, depth + 1)
    else:
        out[sku] += quantity


def explained(db, start, end):
    """Units each product should lose between two moments, from the paid sales read."""
    kits, units, counted, unlinked = _kits(db), defaultdict(Decimal), 0, 0
    for channel, store in db.execute(
        "SELECT DISTINCT channel,store FROM native_order_observations"
    ).fetchall():
        for unit in native_ingest._units(db, channel, store).values():
            for order in unit.values():
                created = (order.get("created_at") or "")[:19]
                status = str(order.get("status") or "").upper()
                if not start[:19] < created <= end[:19]:
                    continue
                if status in native_ingest.CANCELLED or status in UNPAID:
                    continue
                counted += 1
                for item in order.get("items") or []:
                    resolved = native_ingest._resolve(db, store, item, channel)
                    quantity = _number(native_ingest._quantity(channel, item))
                    if not resolved.get("sku") or quantity is None:
                        unlinked += 1
                        continue
                    _expand(kits, resolved["sku"], quantity, units)
    return units, counted, unlinked


OUTSIDE_SQL = (
    "SELECT coalesce(json_agg(t),'[]') FROM (SELECT coalesce(numeroloja,'')::text AS key,"
    " numero::text AS number, item_codigo::text AS sku, item_quantidade AS quantity"
    " FROM davinci.bling_orders WHERE created_at > '{start}' AND created_at <= '{end}'"
    " AND item_codigo IS NOT NULL AND coalesce(situacao,'') <> '12') t"
)


def outside_reader(read=None):
    """Sales Bling took in a window that did not come from a store the ERP reads (Temu,
    internal orders, manual and in-person sales), from DaVinci's copy of Bling, read only."""
    if read is None:
        from app.erp_local.bling_mirror import read_local as read

    def outside(start, end, known):
        rows = read(OUTSIDE_SQL.format(start=start.replace("'", ""), end=end.replace("'", "")))
        units = defaultdict(Decimal)
        orders = set()
        for row in rows:
            if row.get("key") and row["key"] in known:
                continue
            quantity = _number(row.get("quantity"))
            if quantity is None or not row.get("sku"):
                continue
            units[str(row["sku"]).strip().lower()] += quantity
            orders.add(row.get("number"))
        return units, len(orders)

    return outside


def report(db, start=None, end=None, outside=None):
    db.executescript(SCHEMA)
    rows = db.execute("SELECT taken_at,data FROM stock_snapshots ORDER BY taken_at").fetchall()
    if len(rows) < 2:
        return {"state": "waiting", "snapshots": len(rows)}
    last = rows[-1] if end is None else next(r for r in rows if r["taken_at"] == end)
    if start is None:
        older = [r for r in rows if r["taken_at"] <= _shift(last["taken_at"])] or rows[:1]
        first = older[-1]
    else:
        first = next(r for r in rows if r["taken_at"] == start)
    before, after = json.loads(first["data"]), json.loads(last["data"])
    units, counted, unlinked = explained(db, first["taken_at"], last["taken_at"])
    kits = _kits(db)
    outside_orders = 0
    if outside is not None:
        known = set()
        for channel, store in db.execute(
            "SELECT DISTINCT channel,store FROM native_order_observations"
        ).fetchall():
            for key, unit in native_ingest._units(db, channel, store).items():
                known.add(key)
                known.update(unit)
        by_lower = {sku.lower(): sku for sku in kits}
        extra, outside_orders = outside(first["taken_at"], last["taken_at"], known)
        for sku, quantity in extra.items():
            if sku in by_lower:
                _expand(kits, by_lower[sku], quantity, units)
    origin = {}
    for row in db.execute("SELECT sku,data FROM catalog_origin"):
        source = json.loads(row["data"]).get("bling_product_id")
        if source:
            origin[row["sku"]] = str(source)
    bling, erp = defaultdict(Decimal), defaultdict(Decimal)
    derived = set()
    for sku, source in origin.items():
        if kits.get(sku, ("simple",))[0] != "simple" or source not in before or source not in after:
            continue
        if "+" in sku:
            # A combo kept simple here: Bling's balance follows its parts, so it is not stock of
            # its own to explain. Its parts are compared in their own families.
            derived.add(stock_families.key(sku) or sku)
            continue
        family = stock_families.key(sku) or sku
        bling[family] += Decimal(after[source]) - Decimal(before[source])
        erp[family] -= units.get(sku, Decimal(0))
    families = sorted(set(bling) | set(erp))
    moved = [f for f in families if bling[f] or erp[f]]
    same = [f for f in moved if bling[f] == erp[f]]
    differences = sorted(
        (
            {"family": f, "bling": str(bling[f]), "erp": str(erp[f]), "gap": str(bling[f] - erp[f])}
            for f in moved
            if bling[f] != erp[f]
        ),
        key=lambda d: -abs(Decimal(d["gap"])),
    )
    return {
        "state": "ok",
        "from": first["taken_at"],
        "to": last["taken_at"],
        "sales_counted": counted,
        "outside_orders": outside_orders,
        "combos_derived": len(derived),
        "items_unlinked": unlinked,
        "families_moved": len(moved),
        "families_equal": len(same),
        "differences": differences[:60],
    }


def _shift(moment):
    """The moment a day earlier, compared as text like the stored ones."""
    from datetime import datetime, timedelta

    return (datetime.fromisoformat(moment) - timedelta(hours=23)).isoformat()
