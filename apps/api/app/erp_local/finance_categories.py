"""Financial categories, as Bling's "Categorias" of income and expense, with subcategories.

A category is kept on the account itself (any account: manual entry, purchase, sale), so
"Alterar categorias" works on whatever the list shows. Categories are never deleted, only
deactivated, so what was categorised keeps its meaning.
"""

from uuid import uuid4

from app.erp_local.core import ERPError, text

SCHEMA = """
CREATE TABLE IF NOT EXISTS finance_categories (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL, parent_id TEXT,
 active INTEGER NOT NULL, version INTEGER NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS finance_account_categories (
 account_id TEXT PRIMARY KEY REFERENCES accounts(id),
 category_id TEXT NOT NULL REFERENCES finance_categories(id), updated_at TEXT NOT NULL);
"""
COMMANDS = {"finance.category.save", "finance.category.assign"}
KINDS = {"expense": "Despesa", "income": "Receita"}
# Which side of the ledger each account kind belongs to.
ACCOUNT_SIDE = {"receivable": "income"}


def _row(db, identifier):
    return db.execute("SELECT * FROM finance_categories WHERE id=?", (identifier,)).fetchone()


def _save(erp, db, p):
    allowed = {"id", "version", "name", "kind", "parent_id", "active"}
    if not isinstance(p, dict) or set(p) - allowed:
        raise ERPError("category_fields", "Campos da categoria não reconhecidos.")
    name = text(p.get("name"), "nome da categoria", 80)
    kind = p.get("kind")
    if kind not in KINDS:
        raise ERPError("category_kind", "Escolha receita ou despesa.")
    parent = text(p["parent_id"], "categoria pai") if p.get("parent_id") else None
    active = 0 if p.get("active") is False else 1
    previous = _row(db, text(p["id"], "categoria")) if p.get("id") else None
    if p.get("id") and not previous:
        raise ERPError("category_not_found", "Categoria não encontrada.", 404)
    if previous and (isinstance(p.get("version"), bool) or p.get("version") != previous["version"]):
        raise ERPError("category_conflict", "A categoria mudou. Atualize a tela.", 409)
    identifier = previous["id"] if previous else str(uuid4())
    if parent:
        node = _row(db, parent)
        if not node:
            raise ERPError("category_parent", "Categoria pai não encontrada.", 404)
        if node["kind"] != kind:
            raise ERPError("category_parent", "A categoria pai é de outro tipo.")
        seen = set()
        while node:
            if node["id"] == identifier or node["id"] in seen:
                raise ERPError("category_cycle", "Uma categoria não pode ficar dentro dela mesma.")
            seen.add(node["id"])
            node = _row(db, node["parent_id"]) if node["parent_id"] else None
    twin = db.execute(
        "SELECT id FROM finance_categories WHERE kind=? AND coalesce(parent_id,'')=? "
        "AND lower(name)=lower(?) AND id<>?",
        (kind, parent or "", name, identifier),
    ).fetchone()
    if twin:
        raise ERPError("category_duplicate", "Já existe uma categoria com esse nome aqui.", 409)
    version = previous["version"] + 1 if previous else 1
    db.execute(
        "INSERT INTO finance_categories VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
        "name=excluded.name,kind=excluded.kind,parent_id=excluded.parent_id,"
        "active=excluded.active,version=excluded.version,updated_at=excluded.updated_at",
        (identifier, name, kind, parent, active, version, erp.now()),
    )
    return {"id": identifier, "version": version, "external_writes": False}


def _assign(erp, db, p):
    if not isinstance(p, dict) or set(p) != {"account_ids", "category_id"}:
        raise ERPError("category_fields", "Informe as contas e a categoria.")
    ids = p["account_ids"]
    if not isinstance(ids, list) or not 1 <= len(ids) <= 500 or len(set(ids)) != len(ids):
        raise ERPError("category_fields", "Selecione de 1 a 500 contas.")
    category = _row(db, text(p["category_id"], "categoria"))
    if not category or not category["active"]:
        raise ERPError("category_not_found", "Categoria ativa não encontrada.", 404)
    for identifier in ids:
        account = db.execute("SELECT id,kind FROM accounts WHERE id=?", (identifier,)).fetchone()
        if not account:
            raise ERPError("account_not_found", "Conta não encontrada.", 404)
        if ACCOUNT_SIDE.get(account["kind"], "expense") != category["kind"]:
            raise ERPError(
                "category_side", "Conta a receber usa categoria de receita; as demais, de despesa."
            )
        db.execute(
            "INSERT INTO finance_account_categories VALUES (?,?,?) ON CONFLICT(account_id) "
            "DO UPDATE SET category_id=excluded.category_id,updated_at=excluded.updated_at",
            (identifier, category["id"], erp.now()),
        )
    return {"accounts": len(ids), "category_id": category["id"], "external_writes": False}


def apply(erp, db, kind, p):
    return _save(erp, db, p) if kind == "finance.category.save" else _assign(erp, db, p)


def path(db, identifier):
    """'Despesas operacionais › Frete' style name, parents first."""
    names, node = [], _row(db, identifier)
    while node and len(names) < 6:
        names.append(node["name"])
        node = _row(db, node["parent_id"]) if node["parent_id"] else None
    return " › ".join(reversed(names))


def snapshot(db):
    try:
        rows = db.execute("SELECT * FROM finance_categories ORDER BY kind,name").fetchall()
        links = db.execute("SELECT account_id,category_id FROM finance_account_categories")
        assignments = {r["account_id"]: r["category_id"] for r in links}
    except Exception:  # noqa: BLE001 - tables appear with the schema
        return {"categories": [], "assignments": {}}
    return {
        "categories": [
            {**dict(r), "active": bool(r["active"]), "path": path(db, r["id"])} for r in rows
        ],
        "assignments": assignments,
    }
