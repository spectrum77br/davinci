"""Postage objects of the sales read from the stores, laid out like Bling's "Objetos de postagem".

An object is a sale in the shipping phase at its store, or one whose label or tracking the ERP
already read. Nothing here goes to the stores: tracking is refreshed only on request.
"""

import json

from app.erp_local import ml_shipping, native_ingest, stock_families, store_sales

POSTED = {
    "SHIPPED",
    "IN_TRANSIT",
    "TO_CONFIRM_RECEIVE",
    "HANDED_OVER",
    "READY_FOR_PICKUP",
    "NOT_DELIVERED",
}
DELIVERED = {"DELIVERED", "COMPLETED", "COMPLETE", "CONCLUDED"}
SERVICES = {
    "ml": "Mercado Envios",
    "shopee": "Shopee Xpress",
    "tiktok": "TikTok Shop Envios",
    "amazon": "Amazon",
    "magalu": "Magalu Entregas",
    "carrefour": "Carrefour",
}


def situation(channel, statuses, tracking):
    upper = {s.upper() for s in statuses}
    reported = str((tracking or {}).get("status") or "").upper()
    if upper <= native_ingest.CANCELLED:
        return "Cancelado"
    if upper & DELIVERED or reported in DELIVERED:
        return "Entregue"
    if upper & POSTED or reported in POSTED:
        return "Postado"
    if upper & native_ingest.OPEN_STATUS.get(channel, set()):
        return "Não postado"
    return ", ".join(sorted(statuses)) or "Sem situação"


def listing(db, channel="", store="", query="", offset=0, limit=100, lot=""):
    names = store_sales._names(db)
    labels = {
        (r["channel"], r["store"], r["number"]): r["fetched_at"]
        for r in db.execute("SELECT channel,store,number,fetched_at FROM store_labels")
    }
    tracks = {
        (r["channel"], r["store"], r["number"]): json.loads(r["data"])
        for r in db.execute("SELECT channel,store,number,data FROM store_tracking")
    }
    accounts = db.execute("SELECT DISTINCT channel,store FROM native_order_observations").fetchall()
    stores, objects = [], []
    for ch, st in accounts:
        stores.append({"channel": ch, "store": st, "name": names.get((ch, st), st)})
        if (channel and ch != channel) or (store and st != store):
            continue
        for number, unit in native_ingest._units(db, ch, st).items():
            orders = list(unit.values())
            statuses = {str(o.get("status") or "") for o in orders} - {""}
            key = (ch, st, number)
            in_phase = {s.upper() for s in statuses} & native_ingest.OPEN_STATUS.get(ch, set())
            if in_phase and ch == "ml" and ml_shipping.left(db, st, number):
                in_phase = set()
            if not (in_phase or key in labels or key in tracks):
                continue
            captured = native_ingest._unit_recipient(db, ch, st, unit) or {}
            # The lot each item leaves from (.ci, .ra, .sa, .pi, .sp): how the shipping is split.
            lots = sorted(
                {
                    stock_families.lot(part.strip().lower()) or "sem lote"
                    for o in orders
                    for item in o.get("items") or []
                    for part in (native_ingest._resolve(db, st, item, ch).get("sku") or "").split(
                        "+"
                    )
                    if part.strip()
                }
            )
            if lot and lot not in lots:
                continue
            track = tracks.get(key) or {}
            objects.append(
                {
                    "number": number,
                    "channel": ch,
                    "store_id": st,
                    "store": names.get((ch, st), st),
                    "created_at": min((o.get("created_at") or "") for o in orders),
                    "buyer": (captured.get("buyer") or {}).get("name") or "",
                    "service": SERVICES.get(ch, ch),
                    "tracking": track.get("tracking_number") or "",
                    "situation": situation(ch, statuses, track),
                    "label_at": labels.get(key) or "",
                    "tracking_at": bool(track),
                    "lots": lots,
                }
            )
    text = (query or "").strip().casefold()
    if text:
        objects = [
            o
            for o in objects
            if text in " ".join([o["number"], o["store"], o["buyer"], o["tracking"]]).casefold()
        ]
    objects.sort(key=lambda o: o["created_at"], reverse=True)
    stores.sort(key=lambda s: (s["channel"], s["name"].casefold()))
    return {
        "items": objects[offset : offset + limit],
        "total": len(objects),
        "offset": offset,
        "stores": stores,
        "pending": sum(1 for o in objects if o["situation"] == "Não postado"),
    }
