"""Sales read from the stores, for viewing. They are not own orders and reserve nothing.

While Bling owns a store, a sale only becomes an own order at the switch. Until then this list
shows each sale as the store sees it, whether its buyer is kept and what would stop it.
"""

import json

from app.erp_local import native_ingest

REASONS = {
    "cancelled": "Cancelado na loja",
    "not_open": "Fora da fase de envio (não pago ou já enviado)",
    "cart_waiting": "Carrinho em leitura",
    "no_items": "Sem itens",
    "item_missing": "Item sem vínculo com o catálogo",
    "item_ambiguous": "Item com vínculo ambíguo",
    "item_catalog_review": "Produto em conferência",
    "quantity_unknown": "Quantidade não informada",
    "price_unknown": "Preço não informado",
    "recipient_missing": "Comprador não guardado",
}
SETTLED = {"cursor": "9999-12-31T00:00:00+00:00"}


def _names(db):
    return {
        (row["channel"], row["store"]): json.loads(row["data"]).get("name") or row["store"]
        for row in db.execute("SELECT channel,store,data FROM store_policies")
    }


def listing(db, query="", offset=0, limit=50):
    names, sales = _names(db), []
    accounts = db.execute("SELECT DISTINCT channel,store FROM native_order_observations").fetchall()
    for channel, store in accounts:
        try:
            account = dict(native_ingest._account(db, channel, store))
        except Exception:
            account = SETTLED
        for key, unit in native_ingest._units(db, channel, store).items():
            orders = list(unit.values())
            reasons = native_ingest._unit_reasons(db, channel, store, account, key, unit)
            captured = native_ingest._unit_recipient(db, channel, store, unit)
            total = sum(float((o.get("total") or {}).get("amount") or 0) for o in orders)
            skus = sorted(
                {i.get("seller_sku") or "" for o in orders for i in o.get("items") or []} - {""}
            )
            sales.append(
                {
                    "number": key,
                    "channel": channel,
                    "store_id": store,
                    "store": names.get((channel, store), store),
                    "created_at": min((o.get("created_at") or "") for o in orders),
                    "status": ", ".join(sorted({str(o.get("status") or "") for o in orders})),
                    "total": f"{total:.2f}",
                    "buyer": (captured or {}).get("buyer", {}).get("name") or "",
                    "skus": skus,
                    "blocked": [REASONS.get(r, r) for r in sorted(reasons)],
                }
            )
    text = (query or "").strip().casefold()
    if text:
        sales = [
            s
            for s in sales
            if text
            in " ".join([s["number"], s["store"], s["status"], s["buyer"], *s["skus"]]).casefold()
        ]
    sales.sort(key=lambda s: s["created_at"], reverse=True)
    return {"items": sales[offset : offset + limit], "total": len(sales), "offset": offset}
