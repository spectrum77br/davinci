"""Actions on selected products. A batch commits whole or not at all and erases no history.

Removal is reversible: documents, movements and images keep every reference. A product only
becomes a service when no stock or composition depends on it.
"""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from app.erp_local import product_details, product_structures
from app.erp_local.core import ERPError, number, text

SCHEMA = """
CREATE TABLE IF NOT EXISTS product_price_history (
 id INTEGER PRIMARY KEY AUTOINCREMENT, sku TEXT NOT NULL REFERENCES products(sku),
 price_from TEXT, price_to TEXT NOT NULL, operation_key TEXT NOT NULL, actor TEXT NOT NULL,
 created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS product_price_history_sku ON product_price_history(sku,id);
"""
COMMANDS = {
    "product.batch",
    "product.clone",
    "product.delete",
    "product.restore",
    "product.price_adjust",
    "product.structure.replace",
    "product.rename",
}
BATCH_LIMIT = 1000
PRICE_ACTIONS = {"set", "increase", "decrease"}
CENTS = Decimal("0.01")


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


def _decimal(value):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def record_price_change(erp, db, key, sku, before, after, *, always=False):
    """Who changed the ERP's own sale price, when, and from what to what (M-47). The editor's
    save only counts when the price really changed; a mass change keeps every product it set."""
    if after is None:
        return
    if not always and before is not None and _decimal(before) == _decimal(after):
        return
    actor = db.execute("SELECT actor FROM events WHERE operation_key=?", (key,)).fetchone()
    db.execute(
        "INSERT INTO product_price_history(sku,price_from,price_to,operation_key,actor,"
        "created_at) VALUES (?,?,?,?,?,?)",
        (sku, before, str(after), key or "ERP", actor[0] if actor else "ERP", erp.now()),
    )


def price_history(db, sku):
    return [
        dict(r)
        for r in db.execute(
            "SELECT price_from,price_to,actor,operation_key,created_at "
            "FROM product_price_history WHERE sku=? ORDER BY id DESC",
            (sku,),
        )
    ]


def _price_adjust(erp, db, key, p):
    """Novo preço (set, always in R$), Acrescer or Descontar (in R$ or %) on the ERP's own sale
    price. All or nothing, at most 1,000 products, the result in cents. Never published."""
    if set(p) != {"items", "action", "mode", "amount"}:
        raise ERPError("product_price_adjust", "Informe ação, tipo, valor e produtos.")
    if p["action"] not in PRICE_ACTIONS or p["mode"] not in {"value", "percent"}:
        raise ERPError(
            "product_price_adjust", "Escolha novo preço, acrescer ou descontar, em R$ ou %."
        )
    if p["action"] == "set" and p["mode"] != "value":
        raise ERPError("product_price_adjust", "O novo preço é sempre em R$.")
    amount = number(p["amount"], unit=True)
    changes = []
    for sku, version in _selected(erp, db, p["items"]):
        _check_version(product_details.details(db, sku), version)
        row = db.execute("SELECT price FROM product_pricing WHERE sku=?", (sku,)).fetchone()
        before = row["price"] if row else None
        if p["action"] == "set":
            new = amount
        else:
            if before is None or _decimal(before) is None:
                raise ERPError("product_price_missing", "Produto sem preço para reajustar: " + sku)
            old = _decimal(before)
            delta = amount if p["mode"] == "value" else old * amount / 100
            new = old + delta if p["action"] == "increase" else old - delta
            if new < 0:
                raise ERPError(
                    "product_price_negative", "O reajuste deixa negativo o preço de " + sku
                )
        new = new.quantize(CENTS, rounding=ROUND_HALF_UP)
        db.execute(
            "INSERT INTO product_pricing(sku,price,cost_price) VALUES (?,?,NULL) "
            "ON CONFLICT(sku) DO UPDATE SET price=excluded.price",
            (sku, str(new)),
        )
        record_price_change(erp, db, key, sku, before, new, always=True)
        changes.append({"sku": sku, "from": before, "to": str(new)})
    return {"changes": changes, "published": False, "external_writes": False}


def bulk_price(erp, body, actor=None):
    """Produtos 2 › Configuração (M-47): Novo preço, Acrescer or Descontar for every selected
    product, through product.price_adjust (same rules, one event with who did it). Accepted
    while Bling owns the base (consulta.CONSULTA_WRITES); the price goes nowhere else."""
    from uuid import uuid4

    if (
        not isinstance(body, dict)
        or not {"skus", "action", "amount"} <= set(body)
        or set(body) - {"skus", "action", "mode", "amount", "operation_key"}
    ):
        raise ERPError("bulk_price_fields", "Informe os produtos, a ação e o valor.")
    raw = body["skus"]
    if not isinstance(raw, list) or not 1 <= len(raw) <= BATCH_LIMIT:
        raise ERPError("bulk_price_batch", f"Selecione de 1 a {BATCH_LIMIT} produtos.")
    skus = list(dict.fromkeys(text(sku, "SKU") for sku in raw))
    action = body["action"]
    if action not in PRICE_ACTIONS:
        raise ERPError("bulk_price_action", "Escolha novo preço, acrescer ou descontar.")
    mode = "value" if action == "set" else body.get("mode", "value")
    if mode not in {"value", "percent"}:
        raise ERPError("bulk_price_mode", "Escolha R$ ou %.")
    amount = body["amount"]
    if isinstance(amount, bool) or not isinstance(amount, str | int | float):
        raise ERPError("invalid_number", "Informe um número válido.")
    key = body.get("operation_key") or "bulk-price:" + uuid4().hex
    key = text(key, "identificador da operação")
    with erp.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            items = []
            for sku in skus:
                if not db.execute("SELECT 1 FROM products WHERE sku=?", (sku,)).fetchone():
                    raise ERPError("product_not_found", "Produto não encontrado: " + sku, 404)
                items.append({"sku": sku, "version": product_details.details(db, sku)["version"]})
            payload = {"items": items, "action": action, "mode": mode, "amount": str(amount)}
            result = erp.execute_in(
                db, key, "product.price_adjust", payload, *([actor] if actor else [])
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
    count = len(result["changes"])
    return {
        "changed": count,
        "changes": result["changes"],
        "message": f"Preço alterado em {count} produto" + ("" if count == 1 else "s"),
        "replayed": result.get("replayed", False),
    }


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
            key,
        )
    return {"sku": new_sku, "source": sku, "external_writes": False}


def _rename(erp, db, p):
    """The ERP's own code change, as Bling allows: same product, same number for DaVinci, which
    hears it as Bling's produto.alteracao (bling_compat.capture_events). While Bling still owns
    the catalog the change is made there and the ERP follows it (catalog_follow), so here it waits
    for the first store switched to the ERP."""
    import re

    from app.erp_local import bling_mirror, catalog_follow

    if set(p) != {"from", "to"}:
        raise ERPError("product_rename", "Informe o código atual e o novo.")
    if not bling_mirror.switched(db):
        raise ERPError(
            "product_rename_bling",
            "O cadastro ainda segue o Bling: troque o código no Bling e o ERP acompanha.",
            409,
        )
    old = erp.product(db, text(p["from"], "código atual"))["sku"]
    new = text(p["to"], "código novo")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+\-]{0,99}", new):
        raise ERPError("invalid_sku", "SKU contém caracteres não permitidos.")
    if new == old:
        raise ERPError("product_rename", "O código novo é igual ao atual.")
    if db.execute("SELECT 1 FROM products WHERE sku=?", (new,)).fetchone():
        raise ERPError("duplicate_sku", "SKU já cadastrado.", 409)
    return catalog_follow.move_code(db, old, new)


def apply(erp, db, key, kind, p):
    if not isinstance(p, dict):
        raise ERPError("product_batch", "Dados da operação não reconhecidos.")
    if kind == "product.rename":
        return _rename(erp, db, p)
    if kind == "product.batch":
        return _batch(erp, db, key, p)
    if kind == "product.price_adjust":
        return _price_adjust(erp, db, key, p)
    if kind == "product.clone":
        return _clone(erp, db, key, p)
    if kind == "product.structure.replace":
        return _structure_replace(erp, db, key, p)
    return _remove(erp, db, key, p, restore=kind == "product.restore")
