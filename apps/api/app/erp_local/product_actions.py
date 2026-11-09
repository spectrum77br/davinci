"""Actions on selected products. A batch commits whole or not at all and erases no history.

Removal is reversible: documents, movements and images keep every reference. A product only
becomes a service when no stock or composition depends on it.
"""

from decimal import ROUND_HALF_UP, Decimal

from app.erp_local import product_details, product_structures
from app.erp_local.core import ERPError, number, text

COMMANDS = {
    "product.batch",
    "product.clone",
    "product.delete",
    "product.restore",
    "product.price_adjust",
    "product.structure.replace",
}
BATCH_LIMIT = 1000


def _selected(erp, db, raw, limit=BATCH_LIMIT):
    if not isinstance(raw, list) or not 1 <= len(raw) <= limit:
        raise ERPError("product_batch", f"Selecione de 1 a {limit} produtos.")
    rows, seen = [], set()
    for item in raw:
        if not isinstance(item, dict) or set(item) != {"sku", "version"}:
            raise ERPError("product_batch", "Informe produto e versão de cada item.")
        sku = erp.product(db, item["sku"])["sku"]
        if sku in seen:
            raise ERPError("product_batch", "Há produtos repetidos na seleção.")
        seen.add(sku)
        rows.append((sku, item["version"]))
    return rows


def _check_version(current, version):
    if isinstance(version, bool) or version != current["version"]:
        raise ERPError("product_conflict", "O produto mudou. Atualize antes de salvar.", 409)


def _require_no_stock(db, sku, message):
    for row in db.execute("SELECT physical,reserved FROM balances WHERE sku=?", (sku,)):
        if Decimal(row["physical"]) or Decimal(row["reserved"]):
            raise ERPError("product_has_stock", f"{message} {sku} ainda tem estoque.", 409)


def _require_unused(db, sku, message):
    _, recipes = product_structures.graph(db)
    parents = [parent for parent, parts in recipes.items() if any(p["sku"] == sku for p in parts)]
    if parents:
        raise ERPError("product_in_structure", f"{message} {sku} compõe {sorted(parents)[0]}.", 409)


def set_type(erp, db, product, raw):
    if raw not in {"product", "service"}:
        raise ERPError("product_type", "Escolha produto ou serviço.")
    target = "service" if raw == "service" else "simple"
    if product["kind"] == target:
        return
    sku = product["sku"]
    _, recipes = product_structures.graph(db)
    if product["kind"] == "kit" or recipes.get(sku):
        raise ERPError("product_type", "Produto com composição não pode virar serviço.", 409)
    if target == "service":
        _require_no_stock(db, sku, "Para virar serviço,")
        _require_unused(db, sku, "Para virar serviço,")
    db.execute("UPDATE products SET kind=? WHERE sku=?", (target, sku))


def _structure_replace(erp, db, key, p):
    """Bling creates a composed product first and sets its structure in a second call."""
    if set(p) != {"sku", "version", "components", "virtual", "reason"}:
        raise ERPError("structure_fields", "Informe produto, versão, componentes e motivo.")
    product = erp.product(db, p["sku"])
    sku = product["sku"]
    if p["virtual"] and product["kind"] == "simple":
        _require_no_stock(db, sku, "Para virar kit,")
        _require_unused(db, sku, "Para virar kit,")
        db.execute("UPDATE products SET kind='kit' WHERE sku=?", (sku,))
    elif not p["virtual"] and product["kind"] == "kit":
        raise ERPError("structure_kind", "Kit virtual não vira estoque físico por estrutura.", 409)
    product_structures.apply(
        erp,
        db,
        key,
        {
            "sku": sku,
            "version": p["version"],
            "components": p["components"],
            "reason": p["reason"],
        },
    )
    return {"sku": sku, "external_writes": False}


def _batch(erp, db, key, p):
    if set(p) - {"items", "status", "add_tags"} or not {"status", "add_tags"} & set(p):
        raise ERPError("product_batch", "Escolha a alteração dos produtos selecionados.")
    results = []
    for sku, version in _selected(erp, db, p.get("items")):
        data = {}
        if "status" in p:
            data["status"] = p["status"]
        if "add_tags" in p:
            if not isinstance(p["add_tags"], list) or not p["add_tags"]:
                raise ERPError("product_tags", "Informe as tags a incluir.")
            data["tags"] = product_details.details(db, sku)["tags"] + p["add_tags"]
        results.append(
            product_details.apply(erp, db, key, {"sku": sku, "version": version, "data": data})
        )
    return {"updated": len(results), "external_writes": False}


def _price_adjust(erp, db, p):
    if set(p) != {"items", "action", "mode", "amount"}:
        raise ERPError("product_price_adjust", "Informe ação, tipo, valor e produtos.")
    if p["action"] not in {"increase", "decrease"} or p["mode"] not in {"value", "percent"}:
        raise ERPError("product_price_adjust", "Escolha acrescer ou descontar, em R$ ou %.")
    amount = number(p["amount"], unit=True)
    changes = []
    for sku, version in _selected(erp, db, p["items"]):
        _check_version(product_details.details(db, sku), version)
        row = db.execute("SELECT price FROM product_pricing WHERE sku=?", (sku,)).fetchone()
        if not row or row["price"] is None:
            raise ERPError("product_price_missing", "Produto sem preço para reajustar: " + sku)
        old = Decimal(row["price"])
        delta = amount if p["mode"] == "value" else old * amount / 100
        new = old + delta if p["action"] == "increase" else old - delta
        if new < 0:
            raise ERPError("product_price_negative", "O reajuste deixa negativo o preço de " + sku)
        new = new.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        db.execute("UPDATE product_pricing SET price=? WHERE sku=?", (str(new), sku))
        changes.append({"sku": sku, "from": row["price"], "to": str(new)})
    return {"changes": changes, "published": False, "external_writes": False}


def _remove(erp, db, key, p, *, restore):
    if set(p) != {"items"}:
        raise ERPError("product_batch", "Selecione os produtos.")
    for sku, version in _selected(erp, db, p["items"]):
        current = product_details.details(db, sku)
        _check_version(current, version)
        if restore:
            if current["status"] != "deleted":
                raise ERPError("product_not_deleted", "O produto não está excluído: " + sku, 409)
        else:
            if current["status"] == "deleted":
                raise ERPError("product_deleted", "O produto já está excluído: " + sku, 409)
            _require_no_stock(db, sku, "Para excluir,")
            _require_unused(db, sku, "Para excluir,")
        data = {k: v for k, v in current.items() if k != "version"}
        # A restored product stays out of new sales until someone activates it again.
        data["status"] = "inactive" if restore else "deleted"
        product_details.store(erp, db, key, sku, current, data)
    return {"count": len(p["items"]), "external_writes": False}


def _clone(erp, db, key, p):
    from app.erp_local import categories, product_images, product_suppliers, product_tax

    if set(p) != {"sku", "new_sku", "name"}:
        raise ERPError("product_clone", "Informe produto de origem, novo SKU e nome.")
    source = erp.product(db, p["sku"])
    sku = source["sku"]
    pricing = db.execute(
        "SELECT price,cost_price FROM product_pricing WHERE sku=?", (sku,)
    ).fetchone()
    structure = product_structures.details(db, source)
    created = {
        "sku": text(p["new_sku"], "novo SKU"),
        "name": text(p["name"], "nome"),
        "kind": source["kind"],
        "price": (pricing["price"] if pricing else None) or "0",
        "cost_price": (pricing["cost_price"] if pricing else None) or "0",
    }
    if source["kind"] == "kit":
        created["components"] = structure["components"]
    erp.apply(db, key, "product.create", created)
    new_sku = created["sku"]
    if source["kind"] != "kit" and structure["components"]:
        product_structures.apply(
            erp,
            db,
            key,
            {
                "sku": new_sku,
                "version": 0,
                "components": structure["components"],
                "reason": "Clonado de " + sku,
            },
        )
    original = product_details.details(db, sku)
    # Codes identify one item; a clone must not make the barcode reader ambiguous.
    data = {
        k: v
        for k, v in original.items()
        if k not in {"version", "status", "gtin", "gtin_tax", "dun", "weight_kg"}
    }
    product_details.apply(
        erp, db, key, {"sku": new_sku, "version": 0, "data": {**data, "status": "active"}}
    )
    tax = product_tax.details(db, sku)
    if tax["version"]:
        product_tax.apply(
            erp,
            db,
            key,
            {"sku": new_sku, "version": 0, "data": tax["data"], "reason": "Clonado de " + sku},
        )
    suppliers = product_suppliers.details(db, sku)
    if suppliers["rows"]:
        rows = [
            {
                field: row[field]
                for field in [
                    "contact_id",
                    "description",
                    "code",
                    "purchase_price",
                    "cost_price",
                    "warranty_months",
                    "is_default",
                ]
            }
            for row in suppliers["rows"]
        ]
        product_suppliers.apply(
            erp,
            db,
            key,
            {"sku": new_sku, "version": 0, "rows": rows, "reason": "Clonado de " + sku},
        )
    images = product_images.details(db, sku)
    if images["rows"]:
        product_images.apply(
            erp,
            db,
            key,
            "product.images.save",
            {
                "sku": new_sku,
                "version": 0,
                "rows": [{"id": row["id"], "alt": row.get("alt", "")} for row in images["rows"]],
                "reason": "Clonado de " + sku,
            },
        )
    category = categories.document_snapshot(db, sku)
    if category["id"]:
        categories.apply(
            erp,
            db,
            "category.assign",
            {"items": [{"sku": new_sku, "version": 0, "category_id": category["id"]}]},
        )
    return {"sku": new_sku, "source": sku, "external_writes": False}


def apply(erp, db, key, kind, p):
    if not isinstance(p, dict):
        raise ERPError("product_batch", "Dados da operação não reconhecidos.")
    if kind == "product.batch":
        return _batch(erp, db, key, p)
    if kind == "product.price_adjust":
        return _price_adjust(erp, db, p)
    if kind == "product.clone":
        return _clone(erp, db, key, p)
    if kind == "product.structure.replace":
        return _structure_replace(erp, db, key, p)
    return _remove(erp, db, key, p, restore=kind == "product.restore")
