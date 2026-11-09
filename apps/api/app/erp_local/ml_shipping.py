"""Where each Mercado Livre shipment is. The order stays "paid" after it leaves, so only the
shipment tells whether a sale still waits to ship. Read only, from the shipment itself.
"""

import json
from datetime import UTC, datetime, timedelta

from app.erp_local import native_ingest

LEFT = {"shipped", "delivered", "not_delivered", "cancelled"}
RECHECK_MINUTES = 30
WINDOW_DAYS = 90
PER_STORE = 50


def left(db, store, number):
    """True when Mercado Livre says this sale's shipment already left."""
    row = db.execute(
        "SELECT data FROM store_tracking WHERE channel='ml' AND store=? AND number=?",
        (store, number),
    ).fetchone()
    return bool(row) and str(json.loads(row["data"]).get("status") or "").lower() in LEFT


def due(db, now=None):
    """Paid sales of the last days whose shipment was never read, or not read lately."""
    now = now or datetime.now(UTC)
    since = (now - timedelta(days=WINDOW_DAYS)).isoformat()[:19]
    stale = (now - timedelta(minutes=RECHECK_MINUTES)).isoformat()
    known = {
        (r["store"], r["number"]): (json.loads(r["data"]), r["updated_at"])
        for r in db.execute(
            "SELECT store,number,data,updated_at FROM store_tracking WHERE channel='ml'"
        )
    }
    wanted = {}
    for (store,) in db.execute(
        "SELECT DISTINCT store FROM native_order_observations WHERE channel='ml'"
    ).fetchall():
        for number, unit in native_ingest._units(db, "ml", store).items():
            live = [o for o in unit.values() if not native_ingest._is_cancelled(o)]
            if not live or (live[0].get("created_at") or "")[:19] < since:
                continue
            if any(str(o.get("status") or "").upper() != "PAID" for o in live):
                continue
            shipment = live[0].get("shipping_id")
            data, read_at = known.get((store, number), ({}, ""))
            gone = str(data.get("status") or "").lower() in LEFT
            # A shipment that left is read once more only to learn the freight the buyer paid.
            if not shipment or (gone and "cost" in data) or (not gone and read_at > stale):
                continue
            wanted.setdefault(store, {})[str(shipment)] = number
    return {store: dict(list(refs.items())[:PER_STORE]) for store, refs in wanted.items()}


def sync(erp, reader, now=None):
    """One pass: read the due shipments of each store and keep their status with the sale."""
    with erp.connection() as db:
        plan = due(db, now)
    report = {"stores": len(plan), "read": 0, "left": 0, "failed": 0}
    for store, refs in plan.items():
        try:
            got = reader(channel="ml", store=store, refs=list(refs), mode="tracking")
        except Exception:  # noqa: BLE001 - one store failing must not stop the others
            report["failed"] += len(refs)
            continue
        with erp.connection() as db:
            for shipment, number in refs.items():
                data = got.get(shipment) or {}
                if not data or "error" in data:
                    report["failed"] += 1
                    continue
                db.execute(
                    "INSERT OR REPLACE INTO store_tracking VALUES ('ml',?,?,?,?)",
                    (store, number, json.dumps(data), erp.now()),
                )
                report["read"] += 1
                report["left"] += str(data.get("status") or "").lower() in LEFT
    return report
