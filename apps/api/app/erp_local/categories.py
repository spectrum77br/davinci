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
"""
COMMANDS = {"category.save", "category.assign"}


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


def apply(erp, db, kind, p):
    if not isinstance(p, dict):
        raise ERPError("category_fields", "Dados de categoria inválidos.")
    if kind == "category.assign":
        if (
            set(p) != {"items"}
            or not isinstance(p["items"], list)
            or not 1 <= len(p["items"]) <= 100
        ):
            raise ERPError("category_batch", "Selecione de 1 a 100 produtos.")
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
