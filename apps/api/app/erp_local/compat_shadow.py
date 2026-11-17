"""Shadow check of the compatible API: the same questions to Bling and to the ERP, compared.

Before DaVinci points at the ERP (etapa 2), each kind of read it makes of Bling (situations,
depots, sales channels, categories, products by code and by id, balances) is asked of both, on the
same products: Bling through bling_shadow_probe (GET only, no token refresh, DaVinci's own queue)
and the ERP through its compatible API, in this process, on the live base opened read only.
Nothing is written at Bling or at DaVinci. In the ERP's own base only two things: the compatible
numeric ids of depots, categories and products, which the API itself gives on its first read
(`number`), and the result of the round.

Field by field: equal, different, answered by Bling but missing in the ERP, and only in the ERP. A
balance can move between the two reads, and the ERP follows Bling's balance within the hour, so
balance fields are counted apart. Orders, contacts and invoices stay out: they carry buyer data,
and orders only compare after a switch rehearsal.
"""

import json
import os
import re
import secrets
import sqlite3
import time
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from app.erp_local import bling_compat, native_ingest
from app.erp_local import bling_shadow_probe as probe
from app.erp_local.core import ERPError, LocalERP

SCHEMA = "CREATE TABLE IF NOT EXISTS compat_shadow (taken_at TEXT PRIMARY KEY, data TEXT NOT NULL);"
KEEP = 72
SAMPLE = 8
LISTED = 40
EXAMPLES = 3
ROUTES = {
    "situacoes": "Módulos de situação",
    "situacoes_vendas": "Situações de venda",
    "depositos": "Depósitos",
    "canais": "Canais de venda",
    "categorias": "Categorias de produto",
    "produto_codigo": "Produto pelo código",
    "produto": "Produto pelo id",
    "saldos": "Saldos de estoque",
}
MOVING = re.compile(r"saldo\w*", re.IGNORECASE)


class ReadOnlyERP(LocalERP):
    """The live base, only read: no schema work, and SQLite refuses any write."""

    def __init__(self, database):
        self.database = Path(database).resolve()

    @contextmanager
    def connection(self):
        db = sqlite3.connect(
            f"file:{self.database}?mode=ro", uri=True, timeout=15, isolation_level=None
        )
        db.row_factory = sqlite3.Row
        try:
            yield db
        finally:
            db.close()


def pick(erp, now=None):
    """A few active products Bling also has, a different slice each hour."""
    with erp.connection() as db:
        origin = {}
        for row in db.execute("SELECT sku,data FROM catalog_origin"):
            source = json.loads(row["data"]).get("bling_product_id")
            if source:
                origin[row["sku"]] = int(source)
    products = [
        p
        for p in erp.snapshot()["products"]
        if p["sku"] in origin
        and p["details"]["status"] == "active"
        and p.get("kind", "simple") != "service"
    ]
    products.sort(key=lambda p: p["sku"])
    if not products:
        return []
    hour = int((time.time() if now is None else now) // 3600)
    start = (hour * SAMPLE) % len(products)
    chosen = (products + products)[start : start + min(SAMPLE, len(products))]
    return [{"sku": p["sku"], "id": origin[p["sku"]]} for p in chosen]


def number(erp):
    """The compatible numeric ids the API gives on its first read, given here beforehand so the
    read-only answers do not need to write. Depots and categories made in the ERP get ids of their
    own (900.000.000.001 on), never Bling's: the comparison shows it."""
    from app.erp_local import categories

    snap = erp.snapshot()
    with erp.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            for depot in snap["deposits"]:
                bling_compat.numeric_id(db, "depot", depot["id"])
            for category in categories.snapshot(db):
                bling_compat.numeric_id(db, "category", category["id"])
            for product in snap["products"]:
                bling_compat.numeric_id(db, "product", product["sku"])
            db.commit()
        except Exception:
            db.rollback()
            raise


def calls(sample):
    out = [
        ("situacoes", "/situacoes/modulos", []),
        ("situacoes_vendas", f"/situacoes/modulos/{bling_compat.MODULE_SALES}", []),
        ("depositos", "/depositos", []),
        ("canais", "/canais-venda", [["limite", "100"]]),
        ("categorias", "/categorias/produtos", [["limite", "100"]]),
    ]
    for product in sample:
        out.append(("produto_codigo", "/produtos", [["codigo", product["sku"]]]))
        out.append(("produto", f"/produtos/{product['id']}", []))
    if sample:
        out.append(
            ("saldos", "/estoques/saldos", [["idsProdutos[]", str(p["id"])] for p in sample])
        )
    return out


def _key(item, index):
    if isinstance(item, dict):
        if item.get("id") is not None:
            return f"id={item['id']}"
        produto = item.get("produto")
        if isinstance(produto, dict) and produto.get("id") is not None:
            return f"produto={produto['id']}"
    return str(index)


def flatten(value, prefix=""):
    """{path: leaf}; list items are keyed by their id when they have one, so order never counts."""
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            out.update(flatten(item, f"{prefix}.{key}" if prefix else str(key)))
        return out or {prefix: {}}
    if isinstance(value, list):
        out = {}
        for index, item in enumerate(value):
            out.update(flatten(item, f"{prefix}[{_key(item, index)}]"))
        return out or {prefix: []}
    return {prefix: value}


def pattern(path):
    return re.sub(r"\[[^\]]*\]", "[]", path)


def _items(path):
    return re.findall(r"\[([^\]]*)\]", path)


def same(a, b):
    if a == b and type(a) is type(b):
        return True
    numbers = (int, float)
    return (
        isinstance(a, numbers)
        and isinstance(b, numbers)
        and not isinstance(a, bool)
        and not isinstance(b, bool)
        and float(a) == float(b)
    )


def compare(bling, erp):
    """Leaf by leaf. Only list items both sides have are compared; the others are counted."""
    left, right = flatten(bling), flatten(erp)
    left_items = {i for p in left for i in _items(p)[:1]}
    right_items = {i for p in right for i in _items(p)[:1]}
    shared = left_items & right_items
    result = {
        "only_bling_items": len(left_items - right_items),
        "only_erp_items": len(right_items - left_items),
        "fields": [],
    }

    def counted(path):
        first = _items(path)[:1]
        return not first or first[0] in shared

    for path in sorted(set(left) | set(right)):
        if not counted(path):
            continue
        last = path.rsplit(".", 1)[-1].split("[", 1)[0]
        if path not in right:
            kind = "missing"
        elif path not in left:
            kind = "extra"
        elif same(left[path], right[path]):
            kind = "equal"
        elif MOVING.fullmatch(last):
            kind = "moving"
        else:
            kind = "different"
        result["fields"].append((path, kind, left.get(path), right.get(path)))
    return result


def _short(value):
    text = json.dumps(value, ensure_ascii=False)
    return text if len(text) <= 80 else text[:77] + "..."


def summarize(asked, bling_results, erp_results, sample):
    routes = {
        name: {
            "name": label,
            "calls": 0,
            "errors": [],
            "equal": 0,
            "different": 0,
            "missing": 0,
            "extra": 0,
            "moving": 0,
            "only_bling_items": 0,
            "only_erp_items": 0,
        }
        for name, label in ROUTES.items()
    }
    paths = defaultdict(lambda: {"count": 0, "examples": []})
    for (route, path, params), bling, erp in zip(asked, bling_results, erp_results, strict=True):
        row = routes[route]
        row["calls"] += 1
        reference = next((v for k, v in params if k == "codigo"), None) or path
        if bling["status"] != 200 or erp["status"] != 200:
            if len(row["errors"]) < EXAMPLES:
                row["errors"].append(
                    {"ref": reference, "bling": bling["status"], "erp": erp["status"]}
                )
            continue
        found = compare(bling["body"], erp["body"])
        row["only_bling_items"] += found["only_bling_items"]
        row["only_erp_items"] += found["only_erp_items"]
        for field, kind, left, right in found["fields"]:
            row[kind] += 1
            if kind == "equal":
                continue
            entry = paths[(route, pattern(field), kind)]
            entry["count"] += 1
            if len(entry["examples"]) < EXAMPLES:
                entry["examples"].append(
                    {"ref": reference, "bling": _short(left), "erp": _short(right)}
                )
    listed = sorted(paths.items(), key=lambda item: (item[0][2] == "moving", -item[1]["count"]))
    totals = Counter()
    for row in routes.values():
        totals.update({k: row[k] for k in ("calls", "equal", "different", "missing", "extra")})
        totals["moving"] += row["moving"]
        totals["errors"] += len(row["errors"])
    return {
        "sample": [p["sku"] for p in sample],
        "totals": dict(totals),
        "routes": [{"route": k, **v} for k, v in routes.items() if v["calls"]],
        "paths": [
            {"route": route, "path": path, "kind": kind, **data}
            for (route, path, kind), data in listed[:LISTED]
        ],
        "paths_total": len(paths),
    }


@contextmanager
def _compat_client(erp):
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(bling_compat.router(erp))

    @app.exception_handler(ERPError)
    async def domain_error(_request, exc: ERPError):
        return JSONResponse({"code": exc.code, "message": exc.message}, status_code=exc.status)

    token = secrets.token_urlsafe(32)
    previous = os.environ.get(bling_compat.TOKEN_ENV)
    os.environ[bling_compat.TOKEN_ENV] = token
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            client.headers["Authorization"] = f"Bearer {token}"
            yield client
    finally:
        if previous is None:
            os.environ.pop(bling_compat.TOKEN_ENV, None)
        else:
            os.environ[bling_compat.TOKEN_ENV] = previous


def ask_erp(erp, asked):
    results = []
    with _compat_client(erp) as client:
        for _route, path, params in asked:
            response = client.get("/api/erp-local/bling/v3" + path, params=params)
            try:
                body = response.json()
            except ValueError:
                body = None
            results.append({"status": response.status_code, "body": body})
    return results


def host_reader(request):
    script = (Path(__file__).parent / "bling_shadow_probe.py").read_bytes()
    return native_ingest.run_on_host(script, request)


def _valid(read, count):
    if (
        not isinstance(read, dict)
        or read.get("source") != probe.SOURCE
        or read.get("read_only") != "on"
        or read.get("no_token_refresh") is not True
        or read.get("state") not in probe.STATES
        or not isinstance(read.get("results"), list)
    ):
        return False
    if read["state"] != "read_ok":
        return not read["results"]
    return len(read["results"]) == count and all(
        isinstance(r, dict) and set(r) == {"status", "body"} and type(r["status"]) is int
        for r in read["results"]
    )


def run(erp, reader=None, *, live=None, now=None):
    """One round. `erp` keeps the result; `live` (the same base, read only) answers."""
    live = live or ReadOnlyERP(erp.database)
    sample = pick(live, now)
    asked = calls(sample)
    request = {"calls": [{"path": path, "params": params} for _, path, params in asked]}
    number(erp)
    read = (reader or host_reader)(request)
    if not _valid(read, len(asked)):
        raise ERPError("shadow_read_invalid", "Leitura do Bling fora do formato esperado.")
    if read["state"] == "read_ok":
        report = summarize(asked, read["results"], ask_erp(live, asked), sample)
    else:
        report = {"sample": [p["sku"] for p in sample], "totals": {}, "routes": [], "paths": []}
    report["state"] = read["state"]
    taken = datetime.now(UTC).isoformat()
    with erp.connection() as db:
        db.executescript(SCHEMA)
        db.execute(
            "INSERT OR REPLACE INTO compat_shadow VALUES (?,?)",
            (taken, json.dumps(report, ensure_ascii=False, separators=(",", ":"))),
        )
        db.execute(
            "DELETE FROM compat_shadow WHERE taken_at NOT IN "
            "(SELECT taken_at FROM compat_shadow ORDER BY taken_at DESC LIMIT ?)",
            (KEEP,),
        )
    return {"taken_at": taken, "state": read["state"], **report["totals"]}


def latest(db):
    """The last round, with how many rounds of the last three days were kept."""
    try:
        rows = db.execute(
            "SELECT taken_at,data FROM compat_shadow ORDER BY taken_at DESC LIMIT ?", (KEEP,)
        ).fetchall()
    except sqlite3.OperationalError:  # table not made yet
        return None
    if not rows:
        return None
    states = Counter(json.loads(r[1]).get("state") for r in rows)
    return {
        "taken_at": rows[0][0],
        "rounds": len(rows),
        "states": dict(states),
        **json.loads(rows[0][1]),
    }
