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
    pieces = [p.strip() for p in sku.split("+") if p.strip()]
    if kind == "simple" and len(pieces) > 1 and all(p in kits for p in pieces) and depth < 4:
        # A combo kept simple here takes its parts, as Bling's balance of it follows them.
        for piece in pieces:
            _expand(kits, piece, quantity, out, depth + 1)
        return
    if kind == "kit" and parts and depth < 4:
        for part in parts:
            _expand(kits, part["sku"], quantity * Decimal(str(part["quantity"])), out, depth + 1)
    else:
        out[sku] += quantity


def _moment(value):
    """Any timestamp as an aware datetime in UTC; the oldest possible when unreadable."""
    from datetime import UTC, datetime

    text = str(value or "").strip().replace(" ", "T")
    if text.endswith("+00"):
        text += ":00"
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=UTC)
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def explained(db, start, end, bling_window=frozenset(), in_copy=frozenset()):
    """Units each product should lose between two moments, from the paid sales read.

    A sale counts in the window of its time at the store or, when Bling's copy shows Bling took it
    inside the window (`bling_window`, store numbers), in that one: Bling's balance moves when
    Bling takes the sale, which can be later than the store's time."""
    kits, units, counted, unlinked = _kits(db), defaultdict(Decimal), 0, 0
    for channel, store in db.execute(
        "SELECT DISTINCT channel,store FROM native_order_observations"
    ).fetchall():
        for key, unit in native_ingest._units(db, channel, store).items():
            for external, order in unit.items():
                created = (order.get("created_at") or "")[:19]
                status = str(order.get("status") or "").upper()
                if key in in_copy or external in in_copy:
                    # Bling has this sale: its time at Bling decides, as Bling's balance moved then.
                    if not (key in bling_window or external in bling_window):
                        continue
                elif not start[:19] < created <= end[:19]:
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
    " numero::text AS number, item_codigo::text AS sku, item_quantidade AS quantity,"
    " created_at::text AS taken_at"
    " FROM davinci.bling_orders WHERE created_at > ('{start}'::timestamptz - interval '15 days')"
    " AND created_at <= ('{end}'::timestamptz + interval '1 day')"
    " AND item_codigo IS NOT NULL AND coalesce(situacao,'') <> '12') t"
)


def outside_reader(read=None):
    """Sales Bling took in a window that did not come from a store the ERP reads (Temu,
    internal orders, manual and in-person sales), from DaVinci's copy of Bling, read only."""
    if read is None:
        from app.erp_local.bling_mirror import read_local as read

    def outside(start, end, known):
        rows = read(OUTSIDE_SQL.format(start=start.replace("'", ""), end=end.replace("'", "")))
        units, every = defaultdict(Decimal), defaultdict(Decimal)
        orders, keys, present = set(), set(), set()
        lower, upper = _moment(start), _moment(end)
        # The first Bling order of a store number is the sale itself; a later one with the same
        # number (exchange, resend) is Bling stock the store sale does not explain.
        first = {}
        for row in rows:
            if row.get("key") and row.get("number"):
                number = str(row["number"])
                current = first.get(row["key"])
                if current is None or (len(number), number) < (len(current), current):
                    first[row["key"]] = number
        for row in rows:
            if row.get("key"):
                present.add(row["key"])
            if not lower < _moment(row.get("taken_at")) <= upper:
                continue
            quantity = _number(row.get("quantity"))
            if quantity is None or not row.get("sku"):
                continue
            sku = str(row["sku"]).strip().lower()
            every[sku] += quantity
            if row.get("key"):
                keys.add(row["key"])
            extra_order = row.get("key") and str(row.get("number")) != first.get(row["key"])
            if row.get("key") and row["key"] in known and not extra_order:
                continue
            units[sku] += quantity
            orders.add(row.get("number"))
        return units, len(orders), every, (keys, present)

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
    kits = _kits(db)
    outside_orders = 0
    bling_sales = None
    window = in_copy = frozenset()
    if outside is not None:
        known = set()
        for channel, store in db.execute(
            "SELECT DISTINCT channel,store FROM native_order_observations"
        ).fetchall():
            for key, unit in native_ingest._units(db, channel, store).items():
                known.add(key)
                known.update(unit)
        by_lower = {sku.lower(): sku for sku in kits}
        extra, outside_orders, every, (window, in_copy) = outside(
            first["taken_at"], last["taken_at"], known
        )
        window, in_copy = frozenset(window), frozenset(in_copy)
        # What Bling's own orders in the window explain, read the same way: when the ERP agrees
        # with them, a remaining gap is stock that moved without a sale in Bling.
        bling_sales = defaultdict(Decimal)
        for sku, quantity in every.items():
            _expand(kits, by_lower.get(sku, sku), quantity, bling_sales)
    units, counted, unlinked = explained(db, first["taken_at"], last["taken_at"], window, in_copy)
    if outside is not None:
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
    by_sales = defaultdict(Decimal)
    for sku, quantity in (bling_sales or {}).items():
        family = stock_families.key(sku) or sku
        by_sales[family] -= quantity
    differences, without_sale = [], []
    for f in moved:
        if bling[f] == erp[f]:
            continue
        row = {
            "family": f,
            "bling": str(bling[f]),
            "erp": str(erp[f]),
            "gap": str(bling[f] - erp[f]),
        }
        if bling_sales is not None:
            row["bling_sales"] = str(by_sales[f])
        if bling_sales is not None and by_sales[f] == erp[f]:
            without_sale.append(row)
        else:
            differences.append(row)
    differences.sort(key=lambda d: -abs(Decimal(d["gap"])))
    without_sale.sort(key=lambda d: -abs(Decimal(d["gap"])))
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
        # The ERP explains the same as Bling's own orders; Bling's balance also moved by an entry,
        # a return, a manual adjustment or a late copy.
        "families_moved_without_sale": len(without_sale),
        "moved_without_sale": without_sale[:60],
        "differences": differences[:60],
    }


def _shift(moment):
    """The moment a day earlier, compared as text like the stored ones."""
    from datetime import datetime, timedelta

    return (datetime.fromisoformat(moment) - timedelta(hours=23)).isoformat()
