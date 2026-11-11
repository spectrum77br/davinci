"""Every Bling store sale of the last days, looked up in what the ERP read. Hourly, read only.

Bling's side is DaVinci's copy of it (davinci.bling_orders), read on the API host by a read-only
script; only order numbers, store, day, situation and total travel, never the buyer. Each Bling
store shows how many of its sales the ERP has, which are missing and which differ in value. A
Bling store the ERP does not read (Temu, Shein, sales typed by hand) is listed as such, so nothing
is left out without anyone seeing it.
"""

import json
import sqlite3
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from app.erp_local import native_ingest, parallel_check
from app.erp_local.bling_compat import SITUATIONS

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS bling_coverage (taken_at TEXT PRIMARY KEY, data TEXT NOT NULL);"
)
DAYS = 7
KEEP = 48
LISTED = 30
CANCELLED = {"12"}
# Bling situations that mean the sale was undone after it was placed.
UNDONE = {"12", "83955", "83957"}


def _guess(orders):
    """What a Bling store DaVinci does not know is, from the numbers its sales carry."""
    numbers = [o["store_number"] for o in orders]
    if not any(numbers):
        return "pedidos digitados no Bling (sem número de loja)"
    if all(n.startswith("PO-") for n in numbers if n):
        return "Temu"
    if all(n.startswith("GSH") for n in numbers if n):
        return "Shein"
    return "loja sem cadastro no DaVinci"


def compare(db, orders, accounts):
    """`orders`: Bling's sales (see bling_coverage_read); `accounts`: {(channel, store): state}."""
    groups = {}
    for order in orders:
        groups.setdefault(order["bling_store"], []).append(order)
    stores, totals = [], Counter()
    for bling_store, sales in groups.items():
        first = sales[0]
        channel, store = first.get("channel"), first.get("store")
        account = (channel, store) if (channel, store) in accounts else None
        row = {
            "bling_store": bling_store,
            "channel": channel,
            "store": store,
            "name": first.get("name") or _guess(sales),
            "reading": accounts.get(account) if account else None,
            "bling": len(sales),
            "cancelled": sum(1 for s in sales if s["situation"] in CANCELLED),
            "found": 0,
            "missing": 0,
            "equal": 0,
            "different": 0,
            "unread_total": 0,
            "missing_list": [],
            "different_list": [],
        }
        units = native_ingest._units(db, channel, store) if account else {}
        for sale in sorted(sales, key=lambda s: s["number"]):
            unit = units.get(sale["store_number"]) if sale["store_number"] else None
            if unit is None:
                if sale["situation"] in CANCELLED:
                    continue
                row["missing"] += 1
                if len(row["missing_list"]) < LISTED:
                    row["missing_list"].append(_brief(sale))
                continue
            row["found"] += 1
            live = [o for o in unit.values() if not native_ingest._is_cancelled(o)]
            if sale["situation"] in UNDONE or not live:
                continue
            total = parallel_check.sale_total(db, channel, store, sale["store_number"], live)
            if total is None:
                row["unread_total"] += 1
            elif abs(Decimal(total) - Decimal(sale["total"])) <= Decimal("0.01"):
                row["equal"] += 1
            else:
                row["different"] += 1
                if len(row["different_list"]) < LISTED:
                    row["different_list"].append({**_brief(sale), "erp_total": total})
        if not account:
            row["missing"] = row["bling"] - row["cancelled"]
        totals.update(
            {k: row[k] for k in ("bling", "cancelled", "found", "missing", "equal", "different")}
        )
        stores.append(row)
    stores.sort(key=lambda r: (-r["missing"], -r["bling"]))
    days = sorted({o["day"] for o in orders})
    return {
        "from": days[0] if days else None,
        "to": days[-1] if days else None,
        "totals": dict(totals),
        "stores": stores,
    }


def _brief(sale):
    return {
        "number": sale["number"],
        "store_number": sale["store_number"],
        "day": sale["day"],
        "total": sale["total"],
        "situation": SITUATIONS.get(int(sale["situation"]), sale["situation"])
        if sale["situation"].isdigit()
        else sale["situation"],
    }


def host_reader(days=DAYS):
    script = (Path(__file__).parent / "bling_coverage_read.py").read_bytes()
    return native_ingest.run_on_host(script, {"days": days})


def run(erp, reader=None):
    orders = (reader or host_reader)()
    with erp.connection() as db:
        db.executescript(SCHEMA)
        accounts = {
            (r["channel"], r["store"]): r["last_state"]
            for r in db.execute("SELECT channel,store,last_state FROM ingest_accounts")
        }
        report = compare(db, orders, accounts)
        taken = datetime.now(UTC).isoformat()
        db.execute(
            "INSERT OR REPLACE INTO bling_coverage VALUES (?,?)",
            (taken, json.dumps(report, ensure_ascii=False, separators=(",", ":"))),
        )
        db.execute(
            "DELETE FROM bling_coverage WHERE taken_at NOT IN "
            "(SELECT taken_at FROM bling_coverage ORDER BY taken_at DESC LIMIT ?)",
            (KEEP,),
        )
    return {"taken_at": taken, **report["totals"]}


def latest(db):
    try:
        row = db.execute(
            "SELECT taken_at,data FROM bling_coverage ORDER BY taken_at DESC LIMIT 1"
        ).fetchone()
    except sqlite3.OperationalError:  # table not made yet
        return None
    return {"taken_at": row[0], **json.loads(row[1])} if row else None
