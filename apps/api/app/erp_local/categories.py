"""Own category hierarchy and exact channel references, with no publication."""

import json
from uuid import uuid4

from app.erp_local.core import ERPError, dumps, text

SCHEMA = """
CREATE TABLE IF NOT EXISTS own_categories (
 id TEXT PRIMARY KEY, parent_id TEXT REFERENCES own_categories(id),
 name TEXT NOT NULL, status TEXT NOT NULL, version INTEGER NOT NULL,
 data TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS own_product_categories (
 sku TEXT PRIMARY KEY REFERENCES products(sku), category_id TEXT REFERENCES own_categories(id),
 version INTEGER NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS own_product_category_history (
 id INTEGER PRIMARY KEY AUTOINCREMENT, sku TEXT NOT NULL REFERENCES products(sku),
 category_from TEXT, category_to TEXT, operation_key TEXT NOT NULL, actor TEXT NOT NULL,
 created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS own_product_category_history_sku
 ON own_product_category_history(sku,id);
"""
COMMANDS = {"category.save", "category.assign"}
ASSIGN_LIMIT = 100
BULK_LIMIT = 1000


def hierarchy(db, identifier):
    result, seen = [], set()
    while identifier:
        row = db.execute("SELECT * FROM own_categories WHERE id=?", (identifier,)).fetchone()
        if not row:
            raise ERPError("category_not_found", "Categoria não encontrada.", 404)
        if identifier in seen:
            raise ERPError("category_cycle", "A categoria não pode conter a si mesma.", 409)
        seen.add(identifier)
        result.append(dict(row))
        identifier = row["parent_id"]
    return list(reversed(result))


def document_snapshot(db, sku):
    row = db.execute("SELECT * FROM own_product_categories WHERE sku=?", (sku,)).fetchone()
    path = hierarchy(db, row["category_id"]) if row and row["category_id"] else []
    return {
        "version": row["version"] if row else 0,
        "id": row["category_id"] if row else None,
        "path": [{k: r[k] for k in ("id", "name", "version")} for r in path],
    }


def _actor(db, key):
    row = db.execute("SELECT actor FROM events WHERE operation_key=?", (key,)).fetchone()
    return row[0] if row else "ERP"


def apply(erp, db, kind, p, key=None):
    if not isinstance(p, dict):
        raise ERPError("category_fields", "Dados de categoria inválidos.")
    if kind == "category.assign":
        if (
            set(p) != {"items"}
            or not isinstance(p["items"], list)
            or not 1 <= len(p["items"]) <= ASSIGN_LIMIT
        ):
            raise ERPError("category_batch", f"Selecione de 1 a {ASSIGN_LIMIT} produtos.")
        seen = set()
        for item in p["items"]:
            if not isinstance(item, dict) or set(item) != {"sku", "version", "category_id"}:
                raise ERPError("category_fields", "Informe produto, categoria e versão do vínculo.")
            sku = erp.product(db, item["sku"])["sku"]
            if sku in seen:
                raise ERPError("category_batch", "Há produtos repetidos na seleção.")
            seen.add(sku)
            current = document_snapshot(db, sku)
            if type(item["version"]) is not int or item["version"] != current["version"]:
                raise ERPError("category_version", "O vínculo mudou. Atualize a lista.", 409)
            category = text(item["category_id"], "categoria") if item["category_id"] else None
            if category and any(r["status"] != "active" for r in hierarchy(db, category)):
                raise ERPError("category_inactive", "Categoria ou categoria superior inativa.", 409)
            db.execute(
                "INSERT INTO own_product_categories VALUES (?,?,?,?) ON CONFLICT(sku) "
                "DO UPDATE SET category_id=excluded.category_id,version=excluded.version,"
                "updated_at=excluded.updated_at",
                (sku, category, current["version"] + 1, erp.now()),
            )
            if category != current["id"]:
                # Who changed it and from what. A change made in the ERP (not by a load from
                # Bling) also keeps the manual register load from putting Bling's back
                # (catalog_metadata.category_set_here).
                db.execute(
                    "INSERT INTO own_product_category_history(sku,category_from,category_to,"
                    "operation_key,actor,created_at) VALUES (?,?,?,?,?,?)",
                    (sku, current["id"], category, key or "ERP", _actor(db, key), erp.now()),
                )
        return {"skus": sorted(seen), "external_writes": False}
    if set(p) - {"id", "version", "data"} or not isinstance(p.get("data"), dict):
        raise ERPError("category_fields", "Dados de categoria inválidos.")
    raw = p["data"]
    if set(raw) - {"name", "parent_id", "status", "notes", "links"}:
        raise ERPError("category_fields", "Campos de categoria inválidos.")
    identifier = text(p["id"], "categoria") if p.get("id") else str(uuid4())
    previous = db.execute("SELECT * FROM own_categories WHERE id=?", (identifier,)).fetchone()
    if p.get("id") and not previous:
        raise ERPError("category_not_found", "Categoria não encontrada.", 404)
    version = previous["version"] if previous else 0
    if type(p.get("version", 0)) is not int or p.get("version", 0) != version:
        raise ERPError("category_version", "A categoria mudou. Atualize antes de salvar.", 409)
    name, status = text(raw.get("name"), "nome da categoria"), raw.get("status", "active")
    if not isinstance(status, str) or status not in {"active", "inactive"}:
        raise ERPError("category_status", "Situação de categoria inválida.")
    parent = text(raw["parent_id"], "categoria superior") if raw.get("parent_id") else None
    ancestors = hierarchy(db, parent)
    if identifier in {r["id"] for r in ancestors}:
        raise ERPError(
            "category_cycle", "Uma categoria não pode ser filha de si ou de uma descendente.", 409
        )
    siblings = db.execute("SELECT id,name FROM own_categories WHERE parent_id IS ?", (parent,))
    if any(r["id"] != identifier and r["name"].casefold() == name.casefold() for r in siblings):
        raise ERPError(
            "category_duplicate", "Já existe uma categoria com este nome neste nível.", 409
        )
    links = raw.get("links", [])
    if not isinstance(links, list) or len(links) > 100:
        raise ERPError("category_links", "Vínculos de loja inválidos.")
    linked, identities = [], set()
    for link in links:
        if not isinstance(link, dict) or set(link) != {"channel", "store", "external_id"}:
            raise ERPError("category_links", "Informe canal, loja e categoria externa.")
        normalized = {k: text(v, k) for k, v in link.items()}
        identity = (normalized["channel"], normalized["store"])
        policy = db.execute(
            "SELECT status,data FROM store_policies WHERE channel=? AND store=?", identity
        ).fetchone()
        if identity in identities or not policy or policy["status"] != "shadow":
            raise ERPError(
                "category_store", "Vínculo exige uma conta identificada e sem repetição.", 409
            )
        identities.add(identity)
        linked.append({**normalized, "prepared_only": True})
    data = {
        "notes": text(raw["notes"], "observações", 2000) if raw.get("notes") else "",
        "links": linked,
    }
    db.execute(
        "INSERT INTO own_categories VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
        "parent_id=excluded.parent_id,name=excluded.name,status=excluded.status,"
        "version=excluded.version,data=excluded.data,updated_at=excluded.updated_at",
        (identifier, parent, name, status, version + 1, dumps(data), erp.now()),
    )
    return {"id": identifier, "version": version + 1, "external_writes": False}


def by_name(db, name):
    """The ERP category a person picks by name (Produtos 2 › Configuração lists the same names as
    its Categoria filter). A top-level category wins over a sub-category of the same name."""
    wanted = text(name, "categoria").casefold()
    found = [
        r
        for r in db.execute("SELECT id,parent_id,name FROM own_categories")
        if r["name"].casefold() == wanted
    ]
    top = [r for r in found if r["parent_id"] is None]
    chosen = top if top else found
    if not chosen:
        raise ERPError(
            "category_unknown",
            f"Categoria não encontrada no ERP: {name.strip()}. Escolha uma categoria cadastrada.",
        )
    if len(chosen) > 1:
        raise ERPError(
            "category_ambiguous", f"Há mais de uma categoria {name.strip()} no ERP.", 409
        )
    return chosen[0]["id"]


def bulk_assign(erp, body, actor=None):
    """Produtos 2 › Configuração (M-45): one category for every selected product, through
    category.assign in batches of 100, all in one transaction. A product missing, excluded or
    already in that category is skipped with the reason. Accepted while Bling owns the base
    (consulta.CONSULTA_WRITES)."""
    from uuid import uuid4

    from app.erp_local import product_details

    if not isinstance(body, dict) or set(body) != {"skus", "category"}:
        raise ERPError("bulk_category_fields", "Informe os produtos e a categoria.")
    raw = body["skus"]
    if not isinstance(raw, list) or not 1 <= len(raw) <= BULK_LIMIT:
        raise ERPError("bulk_category_batch", f"Selecione de 1 a {BULK_LIMIT} produtos.")
    skus = list(dict.fromkeys(text(sku, "SKU") for sku in raw))
    if not isinstance(body["category"], str):
        raise ERPError("bulk_category_fields", "Escolha a categoria.")
    with erp.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            category = by_name(db, body["category"])
            items, skipped = [], []
            for sku in skus:
                if not db.execute("SELECT 1 FROM products WHERE sku=?", (sku,)).fetchone():
                    skipped.append({"sku": sku, "reason": "Produto não encontrado no ERP."})
                    continue
                if product_details.details(db, sku)["status"] == "deleted":
                    skipped.append({"sku": sku, "reason": "Produto excluído."})
                    continue
                current = document_snapshot(db, sku)
                if current["id"] == category:
                    skipped.append({"sku": sku, "reason": "Já está nesta categoria."})
                    continue
                items.append({"sku": sku, "version": current["version"], "category_id": category})
            key = "bulk-category:" + uuid4().hex
            for number, start in enumerate(range(0, len(items), ASSIGN_LIMIT), 1):
                erp.execute_in(
                    db,
                    f"{key}:{number}",
                    "category.assign",
                    {"items": items[start : start + ASSIGN_LIMIT]},
                    *([actor] if actor else []),
                )
            db.commit()
        except Exception:
            db.rollback()
            raise
    return {
        "changed": len(items),
        "skipped": skipped,
        "message": f"Categoria alterada em {len(items)} produto" + ("" if len(items) == 1 else "s"),
    }


def snapshot(db):
    result = []
    for r in db.execute("SELECT * FROM own_categories ORDER BY name COLLATE NOCASE,id"):
        path = hierarchy(db, r["id"])
        result.append(
            {
                **dict(r),
                "data": json.loads(r["data"]),
                "path": " / ".join(p["name"] for p in path),
                "effective_active": all(p["status"] == "active" for p in path),
            }
        )
    return result


def usage(db, tags=()):
    """How many products (not deleted) use each category name and end with each given tag — what
    DaVinci's Cadastros › Produtos asks before a category or a tag leaves the official list
    (M-29). Read only; DaVinci's API never changes the product → category link (the ERP's own
    screen does, Produtos 2 › Configuração, M-45)."""
    rows = db.execute(
        "SELECT p.sku, d.data AS details, c.name AS category FROM products p "
        "LEFT JOIN product_details d ON d.sku=p.sku "
        "LEFT JOIN own_product_categories pc ON pc.sku=p.sku "
        "LEFT JOIN own_categories c ON c.id=pc.category_id"
    )
    wanted = [t for t in dict.fromkeys(tags) if t]
    names, without, total, ends = {}, 0, 0, dict.fromkeys(wanted, 0)
    for row in rows:
        if row["details"] and json.loads(row["details"]).get("status") == "deleted":
            continue
        total += 1
        if row["category"]:
            names[row["category"]] = names.get(row["category"], 0) + 1
        else:
            without += 1
        sku = row["sku"].casefold()
        for tag in wanted:
            if sku.endswith(tag.casefold()):
                ends[tag] += 1
    return {"products": total, "categories": names, "without_category": without, "tags": ends}
