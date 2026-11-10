"""The catalog kept as Bling has it while Bling still owns the stores.

Every reference refresh, an active product created in Bling comes in with its Bling id, so stock
and sales line up. Combos stay as they are here (kits that take their parts): Bling lists 204 of
them as simple products, but on 10/11/2026 the balance of 172 of the 191 with stock was exactly
their smallest part's, and the other 19 were 1 or 2 units off, so their stock is the parts', not
their own. A new combo with no parts in Bling is reported for a person instead of guessed.
Nothing is written to Bling.
"""

import json
from decimal import Decimal, InvalidOperation

from app.erp_local.core import ERPError, dumps, text

COMMANDS = {"catalog.follow.add"}


def _decimal(value):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number.is_finite() else None


def _add(erp, db, p):
    wanted = {"sku", "name", "kit", "components", "price", "cost_price", "origin"}
    if set(p) != wanted:
        raise ERPError("catalog_follow", "Produto do Bling incompleto.")
    sku = text(p["sku"], "SKU")
    if db.execute("SELECT 1 FROM products WHERE sku=?", (sku,)).fetchone():
        return {"sku": sku, "changed": False, "external_writes": False}
    parts = p["components"] if p["kit"] else []
    for part in parts:
        erp.product(db, part["sku"])
    db.execute(
        "INSERT INTO products VALUES (?,?,?,?)",
        (sku, text(p["name"], "nome", 300), "kit" if p["kit"] else "simple", dumps(parts)),
    )
    db.execute("INSERT INTO catalog_origin VALUES (?,?)", (sku, dumps(p["origin"])))
    db.execute("INSERT INTO product_pricing VALUES (?,?,?)", (sku, p["price"], p["cost_price"]))
    return {"sku": sku, "changed": True, "external_writes": False}


def apply(erp, db, key, kind, p):
    if not isinstance(p, dict):
        raise ERPError("catalog_follow", "Dados não reconhecidos.")
    return _add(erp, db, p)


def follow(erp, products, components):
    """One pass over DaVinci's mirror of Bling: what came in, and what a person must look at."""
    report = {"created": 0, "refused": {}}
    with erp.connection() as db:
        origin = {}
        for row in db.execute("SELECT sku,data FROM catalog_origin"):
            source = json.loads(row["data"]).get("bling_product_id")
            if source:
                origin[str(source)] = row["sku"]
    by_id = {str(p.get("bling_product_id")): p for p in products if p.get("bling_product_id")}
    parts_of = {}
    for c in components:
        parts_of.setdefault(str(c["kit_bling_product_id"]), []).append(c)
    active = [p for p in by_id.values() if p.get("situacao") == "A" and p.get("sku")]
    # Simple products first, so a new kit finds its parts already here.
    for p in sorted(active, key=lambda p: p.get("formato") == "E"):
        bid, sku = str(p["bling_product_id"]), origin.get(str(p["bling_product_id"]))
        try:
            if sku is None:
                kit = p.get("formato") == "E"
                if not kit and "+" in p["sku"]:
                    raise ERPError("catalog_follow", "combo sem peças no Bling")
                parts = []
                for c in parts_of.get(bid, []) if kit else []:
                    part = origin.get(str(c["component_bling_product_id"]))
                    if not part:
                        raise ERPError("catalog_follow", "peça fora do catálogo")
                    parts.append({"sku": part, "quantity": str(_decimal(c["quantity"]))})
                if kit and not parts:
                    raise ERPError("catalog_follow", "kit sem peças no espelho")
                erp.command(
                    f"catalog-follow-v1:add:{bid}",
                    "catalog.follow.add",
                    {
                        "sku": p["sku"].strip(),
                        "name": p.get("name") or p["sku"],
                        "kit": kit,
                        "components": parts,
                        "price": p.get("price"),
                        "cost_price": p.get("bling_cost_price") or p.get("cost_price"),
                        "origin": {
                            "source_id": p.get("id"),
                            "bling_product_id": p["bling_product_id"],
                            "sellable": True,
                            "catalog_state": "ready",
                            "stock_state": "unknown",
                            "stock_basis": "catalog_follow",
                            "source_status": p.get("situacao"),
                            "source_updated_at": p.get("updated_at"),
                        },
                    },
                    actor="cadastro do Bling",
                )
                origin[bid] = p["sku"].strip()
                report["created"] += 1
        except ERPError as error:
            report["refused"][p["sku"]] = error.message[:80]
    return report
