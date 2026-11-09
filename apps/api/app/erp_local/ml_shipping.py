"""Where each Mercado Livre shipment is. The order stays "paid" after it leaves, so only the
shipment tells whether a sale still waits to ship. Read only, from the shipment itself.
"""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

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


def seller_coupon(payload):
    """The seller's share of the coupons in /orders/{id}/discounts. Price discounts are already in
    the item price, and coupons Mercado Livre pays stay out of Bling's total (37 of 37, 09/11)."""
    total = Decimal(0)
    for detail in (payload or {}).get("details") or []:
        if detail.get("type") != "coupon":
            continue
        for item in detail.get("items") or []:
            total += Decimal(str((item.get("amounts") or {}).get("seller") or 0))
    return total


def discounts_due(db):
    """Sales whose shipment was read but whose seller coupons were not: store → order → sale."""
    wanted = {}
    for row in db.execute(
        "SELECT store,number,data FROM store_tracking WHERE channel='ml'"
    ).fetchall():
        data = json.loads(row["data"])
        if "cost" not in data or "seller_coupon" in data:
            continue
        unit = native_ingest._units(db, "ml", row["store"]).get(row["number"]) or {}
        orders = wanted.setdefault(row["store"], {})
        if len(orders) + len(unit) > PER_STORE:
            continue
        for external in unit:
            orders[str(external)] = row["number"]
    return {store: orders for store, orders in wanted.items() if orders}


def sync_discounts(erp, reader):
    """Read who pays each coupon and keep the seller's share with the sale."""
    with erp.connection() as db:
        plan = discounts_due(db)
    report = {"stores": len(plan), "sales": 0, "failed": 0}
    for store, orders in plan.items():
        try:
            got = reader(channel="ml", store=store, refs=list(orders), mode="discounts")
        except Exception:  # noqa: BLE001 - one store failing must not stop the others
            report["failed"] += len(orders)
            continue
        per_sale, broken = {}, set()
        for external, number in orders.items():
            reply = got.get(external) or {}
            if "error" in reply and "404" not in str(reply["error"]):
                broken.add(number)
                continue
            # 404: Mercado Livre has no discount for this order.
            per_sale[number] = per_sale.get(number, Decimal(0)) + (
                Decimal(0) if "error" in reply else seller_coupon(reply)
            )
        with erp.connection() as db:
            for number, value in per_sale.items():
                if number in broken:
                    continue
                row = db.execute(
                    "SELECT data FROM store_tracking WHERE channel='ml' AND store=? AND number=?",
                    (store, number),
                ).fetchone()
                data = {**json.loads(row["data"]), "seller_coupon": str(value)}
                db.execute(
                    "UPDATE store_tracking SET data=? WHERE channel='ml' AND store=? AND number=?",
                    (json.dumps(data), store, number),
                )
                report["sales"] += 1
        report["failed"] += len(broken)
    return report
