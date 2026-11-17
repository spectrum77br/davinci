"""Every store listing inside Produtos, with its link to the catalog (Novos procedimentos 8).

The listings are DaVinci's (`reference_rows`, kind `links`). Each one gets a situation against the
ERP catalog, which is what "Vincular" shows: linked, the listing's SKU differs from the product's,
the product is not in the ERP, the product is inactive, or the store is archived. "Organizar" hides
the invalid ones here only, never in a marketplace, in DaVinci or in Bling, and only for an
administrator; hidden rows stay listed under "Ocultos" and can come back.
"""

import json
import sqlite3

from app.erp_local.core import ERPError, text

COMMANDS = {"product.listings.organize", "product.listings.show"}
SCHEMA = """
CREATE TABLE IF NOT EXISTS listing_hidden (
 id TEXT PRIMARY KEY, reason TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL);
"""
STATES = {
    "linked": "Vinculado",
    "sku_differs": "SKU do anúncio diferente",
    "excluded": "Produto excluído no Bling",
    "not_in_erp": "Produto fora do ERP",
    "inactive": "Produto inativo",
    "archived": "Loja arquivada",
}
INVALID = {"excluded", "not_in_erp", "inactive", "archived"}
PAGE = 100


def _rows(db, kind):
    return [
        json.loads(r[0])
        for r in db.execute("SELECT data FROM reference_rows WHERE kind=?", (kind,))
    ]


def _catalog(db):
    status = {}
    for row in db.execute("SELECT sku FROM products"):
        status[row[0].lower()] = "active"
    for row in db.execute("SELECT sku,data FROM product_details"):
        status[row[0].lower()] = json.loads(row[1]).get("status") or "active"
    return status


def classify(db):
    try:
        products = {p["id"]: p for p in _rows(db, "products")}
        stores = {i["id"]: i for i in _rows(db, "integrations")}
        links = _rows(db, "links")
    except sqlite3.OperationalError:
        return []
    try:
        hidden = {r[0]: r[1] for r in db.execute("SELECT id,reason FROM listing_hidden")}
    except sqlite3.OperationalError:
        hidden = {}
    catalog = _catalog(db)
    out = []
    for link in links:
        product = products.get(link.get("product_id")) or {}
        sku = str(product.get("sku") or "").strip()
        store = stores.get(link.get("integration_id")) or {}
        listing_sku = str(link.get("external_sku") or "").strip()
        if store.get("archived_at"):
            state = "archived"
        elif str(product.get("situacao") or "").upper() == "E":
            # The listing still points to a product excluded in Bling (5.472 of them on 12/11/2026,
            # many with stock on the store): nothing to link it to until it is linked again.
            state = "excluded"
        elif sku.lower() not in catalog:
            state = "not_in_erp"
        elif catalog[sku.lower()] != "active":
            state = "inactive"
        elif listing_sku and listing_sku.lower() != sku.lower():
            state = "sku_differs"
        else:
            state = "linked"
        out.append(
            {
                "id": link["id"],
                "channel": link.get("platform"),
                "store": store.get("name") or "",
                "listing": link.get("external_id"),
                "variation": link.get("variation_id") or "",
                "listing_sku": listing_sku,
                "sku": sku,
                "title": link.get("listing_title") or "",
                "stock": link.get("stock"),
                "price": link.get("price"),
                "state": state,
                "hidden": link["id"] in hidden,
            }
        )
    return out


def overview(db, *, state="", query="", offset=0, hidden=False):
    rows = classify(db)
    counts = dict.fromkeys(STATES, 0)
    for r in rows:
        if not r["hidden"]:
            counts[r["state"]] += 1
    counts["hidden"] = sum(1 for r in rows if r["hidden"])
    wanted = [r for r in rows if r["hidden"] == hidden and (not state or r["state"] == state)]
    if query:
        needle = query.strip().lower()
        wanted = [
            r
            for r in wanted
            if needle
            in " ".join(
                str(r[k] or "") for k in ("listing", "listing_sku", "sku", "title", "store")
            ).lower()
        ]
    wanted.sort(key=lambda r: (r["state"] == "linked", r["store"], str(r["listing"])))
    return {"counts": counts, "total": len(wanted), "rows": wanted[offset : offset + PAGE]}


def apply(erp, db, key, kind, p):
    from app.erp_local import access

    actor = db.execute("SELECT actor FROM events WHERE operation_key=?", (key,)).fetchone()
    person = access.who(db, actor[0] if actor else "") or {}
    if access.enforced() and not person.get("admin"):
        raise ERPError("listings_admin", "Só um administrador organiza os anúncios.", 403)
    db.executescript(SCHEMA)
    if kind == "product.listings.show":
        if not isinstance(p, dict) or set(p) != {"ids"} or not isinstance(p["ids"], list):
            raise ERPError("listings_fields", "Informe os anúncios que voltam à lista.")
        shown = 0
        for identifier in p["ids"][:5000]:
            shown += db.execute(
                "DELETE FROM listing_hidden WHERE id=?", (text(identifier, "anúncio"),)
            ).rowcount
        return {"shown": shown, "external_writes": False}
    if not isinstance(p, dict) or set(p) != {"states"} or not isinstance(p["states"], list):
        raise ERPError("listings_fields", "Escolha quais anúncios inválidos ocultar.")
    states = set(p["states"])
    if not states or states - INVALID:
        raise ERPError("listings_fields", "Só anúncios inválidos podem ser ocultados.")
    hidden = 0
    for row in classify(db):
        if row["state"] in states and not row["hidden"]:
            db.execute(
                "INSERT INTO listing_hidden VALUES (?,?,?,?)",
                (row["id"], row["state"], actor[0] if actor else "ERP", erp.now()),
            )
            hidden += 1
    return {"hidden": hidden, "external_writes": False}
