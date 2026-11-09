"""Versioned virtual kits and physical assembly recipes.

The live graph determines new documents and stock previews. Saved orders and
production orders keep their own component quantities; no reservation is moved
when a recipe changes. A physical recipe never creates assembled stock.
"""

import json
from decimal import Decimal

from app.erp_local.core import ERPError, dumps, number, text

SCHEMA = """
CREATE TABLE IF NOT EXISTS product_structures (
 sku TEXT PRIMARY KEY REFERENCES products(sku), version INTEGER NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS product_structure_history (
 sku TEXT REFERENCES products(sku), version INTEGER NOT NULL, data TEXT NOT NULL,
 operation_key TEXT REFERENCES events(operation_key), created_at TEXT NOT NULL,
 PRIMARY KEY(sku,version));
"""
COMMANDS = {"product.structure.save"}


def details(db, product):
    row = db.execute("SELECT * FROM product_structures WHERE sku=?", (product["sku"],)).fetchone()
    if row:
        return {"version": row["version"], **json.loads(row["data"])}
    parts = json.loads(product["components"]) if product["kind"] == "kit" else []
    return {
        "version": 0,
        "mode": "virtual" if product["kind"] == "kit" else "physical",
        "components": parts,
        "expanded": parts,
        "reason": "",
        "updated_at": None,
    }


def graph(db):
    products = {r["sku"]: dict(r) for r in db.execute("SELECT * FROM products")}
    recipes = {
        r["sku"]: json.loads(r["data"])["components"]
        for r in db.execute("SELECT * FROM product_structures")
    }
    for sku, product in products.items():
        recipes.setdefault(
            sku, json.loads(product["components"]) if product["kind"] == "kit" else []
        )
    return products, recipes


def expand(products, recipes, sku):
    result = {}

    def walk(identifier, quantity, path):
        if identifier in path or len(path) >= 20:
            raise ERPError("structure_cycle", "A estrutura contém ciclo ou ultrapassa 20 níveis.")
        if identifier not in products:
            raise ERPError("structure_product", "Componente não encontrado: " + identifier)
        if products[identifier]["kind"] != "kit":
            result[identifier] = result.get(identifier, Decimal(0)) + quantity
            return
        if not recipes[identifier]:
            raise ERPError("structure_empty", "Kit virtual sem composição: " + identifier)
        for part in recipes[identifier]:
            walk(part["sku"], quantity * Decimal(part["quantity"]), (*path, identifier))

    for part in recipes[sku]:
        walk(part["sku"], Decimal(part["quantity"]), (sku,))
    if len(result) > 200:
        raise ERPError("structure_size", "A composição expandida ultrapassa 200 peças distintas.")
    return [{"sku": s, "quantity": str(number(q))} for s, q in sorted(result.items())]


def apply(erp, db, key, payload):
    from app.erp_local.channels import capture_stock_changes
    from app.erp_local.product_details import details as metadata

    if not isinstance(payload, dict) or set(payload) != {"sku", "version", "components", "reason"}:
        raise ERPError("structure_fields", "Informe produto, versão, componentes e motivo.")
    sku = text(payload["sku"], "SKU")
    product = erp.product(db, sku)
    current = details(db, product)
    if type(payload["version"]) is not int or payload["version"] != current["version"]:
        raise ERPError("structure_version", "A estrutura mudou. Atualize antes de salvar.", 409)
    origin = erp.origin(db, sku)
    if origin and origin["catalog_state"] != "ready":
        raise ERPError("structure_review", "Concilie a identidade do produto antes de editar.", 409)
    reason = text(payload["reason"], "motivo da alteração da estrutura", 500)
    raw = payload["components"]
    if not isinstance(raw, list) or len(raw) > 200 or product["kind"] == "kit" and not raw:
        raise ERPError(
            "structure_items", "Informe até 200 componentes; kit virtual não fica vazio."
        )
    parts, seen = [], set()
    for item in raw:
        if not isinstance(item, dict) or set(item) != {"sku", "quantity"}:
            raise ERPError("structure_fields", "Informe somente SKU e quantidade do componente.")
        part = text(item["sku"], "SKU do componente")
        if part == sku or part in seen:
            raise ERPError(
                "structure_cycle", "A estrutura repete uma peça ou contém o próprio produto."
            )
        seen.add(part)
        if erp.product(db, part)["kind"] == "service":
            raise ERPError("structure_service", "Serviço não compõe estoque físico: " + part)
        own = erp.origin(db, part)
        settings = metadata(db, part)
        if (
            settings["status"] != "active"
            and (settings["version"] or not own)
            or own
            and own["catalog_state"] != "ready"
        ):
            raise ERPError("structure_review", "Componente inativo ou pendente: " + part, 409)
        parts.append({"sku": part, "quantity": str(number(item["quantity"]))})
    products, recipes = graph(db)
    recipes[sku] = parts
    affected = {sku}
    if product["kind"] == "kit":
        # Physical recipes can use a virtual subkit, but a physical SKU itself
        # remains a leaf when used by another recipe.
        virtual_changed = {sku}
        while True:
            parents = {
                s
                for s, children in recipes.items()
                if any(c["sku"] in virtual_changed for c in children)
            } - affected
            if not parents:
                break
            affected |= parents
            virtual_changed |= {s for s in parents if products[s]["kind"] == "kit"}
    expanded = {s: expand(products, recipes, s) for s in affected}
    # Validate every leaf too: an inherited virtual structure must not smuggle
    # an inactive/ambiguous product into new documents.
    for part in {p["sku"] for values in expanded.values() for p in values}:
        own = erp.origin(db, part)
        settings = metadata(db, part)
        if (
            settings["status"] != "active"
            and (settings["version"] or not own)
            or own
            and own["catalog_state"] != "ready"
        ):
            raise ERPError("structure_review", "Peça expandida inativa ou pendente: " + part, 409)
    actor = db.execute("SELECT actor FROM events WHERE operation_key=?", (key,)).fetchone()[0]
    changed_at = erp.now()
    for parent in sorted(affected):
        before = details(db, products[parent])
        if before["version"] == 0:
            db.execute(
                "INSERT INTO product_structure_history VALUES (?,?,?,?,?)",
                (parent, 0, dumps(before), key, changed_at),
            )
        value = {
            "mode": before["mode"],
            "components": recipes[parent],
            "expanded": expanded[parent],
            "reason": reason,
            "actor": actor,
            "updated_at": changed_at,
            "cause_sku": sku,
        }
        version = before["version"] + 1
        db.execute(
            "INSERT INTO product_structures VALUES (?,?,?) ON CONFLICT(sku) "
            "DO UPDATE SET version=excluded.version,data=excluded.data",
            (parent, version, dumps(value)),
        )
        db.execute(
            "INSERT INTO product_structure_history VALUES (?,?,?,?,?)",
            (parent, version, dumps({"version": version, **value}), key, changed_at),
        )
        if products[parent]["kind"] == "kit":
            db.execute(
                "UPDATE products SET components=? WHERE sku=?", (dumps(expanded[parent]), parent)
            )
    capture_stock_changes(
        erp, db, key, changed_skus={s for s in affected if products[s]["kind"] == "kit"}
    )
    return {
        "sku": sku,
        "version": current["version"] + 1,
        "affected": sorted(affected),
        "external_writes": False,
        "stock_moved": False,
    }


def history(db, sku):
    return [
        {"version": r["version"], "data": json.loads(r["data"]), "created_at": r["created_at"]}
        for r in db.execute(
            "SELECT * FROM product_structure_history WHERE sku=? ORDER BY version DESC LIMIT 20",
            (sku,),
        )
    ]
