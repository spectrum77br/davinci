"""The catalog kept as Bling has it while Bling still owns the stores.

Every reference refresh, an active product created in Bling comes in with its Bling id, so stock
and sales line up. Combos stay as they are here (kits that take their parts): Bling lists 204 of
them as simple products, but on 10/11/2026 the balance of 172 of the 191 with stock was exactly
their smallest part's, and the other 19 were 1 or 2 units off, so their stock is the parts', not
their own. A new combo with no parts in Bling is reported for a person instead of guessed.
Situation follows Bling too: a product inactivated or excluded there turns inactive here (and
active again if Bling reactivates it), so its listings get 0 instead of a stock nobody sells.
While Bling owns the catalog its situation wins; once a store is switched this stops.
Nothing is written to Bling.
"""

import json
from decimal import Decimal, InvalidOperation

from app.erp_local.core import ERPError, dumps, text

COMMANDS = {"catalog.follow.add", "catalog.follow.status"}
# Bling's situation letter → the ERP's product status. Excluded is inactive here: reversible,
# and the history and balances stay.
FOLLOWED = {"A": "active", "I": "inactive", "E": "inactive"}


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


def _status(erp, db, key, p):
    from app.erp_local import bling_mirror, product_details
    from app.erp_local.channels import capture_stock_changes

    if set(p) != {"sku", "status", "source_status"} or p["status"] not in {"active", "inactive"}:
        raise ERPError("catalog_follow", "Situação do Bling não reconhecida.")
    if bling_mirror.switched(db):
        raise ERPError(
            "catalog_follow_stopped", "Há loja trocada para o ERP; o cadastro é do ERP.", 409
        )
    sku = erp.product(db, p["sku"])["sku"]
    row = db.execute("SELECT data FROM catalog_origin WHERE sku=?", (sku,)).fetchone()
    if row:
        origin = {
            **json.loads(row["data"]),
            "source_status": text(p["source_status"], "situação", 2),
            "sellable": p["status"] == "active",
        }
        db.execute("UPDATE catalog_origin SET data=? WHERE sku=?", (dumps(origin), sku))
    current = product_details.details(db, sku)
    if current["status"] in {"deleted", p["status"]}:
        return {"sku": sku, "changed": False, "external_writes": False}
    data = {k: v for k, v in current.items() if k != "version"}
    product_details.store(erp, db, key, sku, current, {**data, "status": p["status"]})
    capture_stock_changes(erp, db, key, changed_skus={sku})
    return {"sku": sku, "status": p["status"], "changed": True, "external_writes": False}


def apply(erp, db, key, kind, p):
    if not isinstance(p, dict):
        raise ERPError("catalog_follow", "Dados não reconhecidos.")
    if kind == "catalog.follow.status":
        return _status(erp, db, key, p)
    return _add(erp, db, p)


def follow_status(erp, products):
    """Bling's situation onto the products already here; any row active keeps it active."""
    from app.erp_local import bling_mirror, product_details

    report = {"changed": 0, "refused": {}}
    with erp.connection() as db:
        if bling_mirror.switched(db):
            return {"state": "stopped"}
        origin = {}
        for row in db.execute("SELECT sku,data FROM catalog_origin"):
            source = json.loads(row["data"]).get("bling_product_id")
            if source:
                origin[str(source)] = row["sku"]
        status = {sku: product_details.details(db, sku)["status"] for sku in origin.values()}
    letters, stamps = {}, {}
    for p in products:
        bid = str(p.get("bling_product_id") or "")
        letter = str(p.get("situacao") or "").upper()
        if bid in origin and letter in FOLLOWED:
            letters.setdefault(bid, set()).add(letter)
            stamps[bid] = max(stamps.get(bid, ""), str(p.get("updated_at") or ""))
    for bid, found in sorted(letters.items()):
        letter = "A" if "A" in found else sorted(found)[0]
        sku, wanted = origin[bid], FOLLOWED[letter]
        if status.get(sku) in {wanted, "deleted"}:
            continue
        try:
            erp.command(
                f"catalog-follow-v1:status:{bid}:{wanted}:{stamps[bid][:26]}",
                "catalog.follow.status",
                {"sku": sku, "status": wanted, "source_status": letter},
                actor="cadastro do Bling",
            )
            report["changed"] += 1
        except ERPError as error:
            report["refused"][sku] = error.message[:80]
    return report


def follow(erp, products, components):
    """One pass over DaVinci's mirror of Bling: what came in, and what a person must look at.
    Once a store is switched the catalog is the ERP's: nothing more comes from Bling."""
    from app.erp_local import bling_mirror

    report = {"created": 0, "refused": {}, "renamed": {}}
    with erp.connection() as db:
        if bling_mirror.switched(db):
            return {"state": "stopped"}
        origin = {}
        for row in db.execute("SELECT sku,data FROM catalog_origin"):
            source = json.loads(row["data"]).get("bling_product_id")
            if source:
                origin[str(source)] = row["sku"]
    by_id = {str(p.get("bling_product_id")): p for p in products if p.get("bling_product_id")}
    # A code changed in Bling for a product already here (b028.24 → z002800.mala, 12/11/2026) is
    # reported, not applied: the code is the key of balances, movements, kits and fiscal data, so
    # a person decides before anything is renamed.
    for bid, here in origin.items():
        there = str((by_id.get(bid) or {}).get("sku") or "").strip()
        if there and there.lower() != here.lower():
            report["renamed"][here] = there
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
