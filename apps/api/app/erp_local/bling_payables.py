"""Bling's open payables, copied read only while Bling still keeps the money side.

The team pays its bills in Bling's Contas a pagar (171 in the November screen, with partial and
recurring payments). Until the switch the ERP only shows them: nothing is written back, and the
copy travels the same read-only way as the shadow check (DaVinci's Bling login on the server, GET
only, in DaVinci's own rate slot, never a token refresh). At the switch these are the bills that
become the ERP's own (Novos procedimentos 13 leaves that transfer for later).

Bling refuses the financial category names to DaVinci's app (403), so a category shows by its id.
Suppliers come by id, only the ones these bills point to, and only the name is kept.
"""

import json
import sqlite3
from datetime import UTC, datetime

from app.erp_local import compat_shadow

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS bling_payables (id TEXT PRIMARY KEY, listed TEXT NOT NULL,"
    " detail TEXT, seen_at TEXT NOT NULL);"
    "CREATE TABLE IF NOT EXISTS bling_payable_contacts (id TEXT PRIMARY KEY, name TEXT NOT NULL);"
    "CREATE TABLE IF NOT EXISTS bling_payables_runs (taken_at TEXT PRIMARY KEY,"
    " data TEXT NOT NULL);"
)
# Bling's account situations: 1 open, 2 paid, 3 partly paid, 4 returned, 5 cancelled.
OPEN = {"1": "Em aberto", "3": "Pago parcialmente"}
PAGE = 100
PAGES_PER_READ = 4
MAX_PAGES = 30
BATCH = 20  # under the read script's 40 calls, short so DaVinci keeps its turn
MAX_DETAILS = 140  # per round: a first copy of a few hundred bills spreads over a few rounds
# Once complete, the list is read again every half hour: bills change slowly and each read takes
# DaVinci's Bling turn. While details are missing, every worker round goes on.
REST_MINUTES = 30


class UnavailableError(Exception):
    pass


def _read(reader, calls):
    read = reader({"calls": [{"path": path, "params": params} for path, params in calls]})
    if not compat_shadow._valid(read, len(calls)) or read.get("state") != "read_ok":
        raise UnavailableError(
            read.get("state") if isinstance(read, dict) else "provider_unavailable"
        )
    for result in read["results"]:
        if result["status"] not in {200, 404}:
            raise UnavailableError(f"http_{result['status']}")
    return read["results"]


def _data(result):
    body = result.get("body")
    return body.get("data") if isinstance(body, dict) else None


def _list(reader):
    listed = {}
    for situation in OPEN:
        first = 1
        while first <= MAX_PAGES:
            pages = range(first, min(first + PAGES_PER_READ, MAX_PAGES + 1))
            calls = [
                (
                    "/contas/pagar",
                    [["pagina", str(p)], ["limite", str(PAGE)], ["situacoes[]", situation]],
                )
                for p in pages
            ]
            last = False
            for result in _read(reader, calls):
                rows = _data(result) or []
                for row in rows:
                    if isinstance(row, dict) and row.get("id"):
                        listed[str(row["id"])] = row
                if len(rows) < PAGE:
                    last = True
                    break
            if last:
                break
            first += PAGES_PER_READ
    return listed


def _batches(items, size=BATCH):
    return [items[i : i + size] for i in range(0, len(items), size)]


def run(erp, reader=None, *, now=None, max_details=MAX_DETAILS):
    """One round: the open list, the detail of what is new or changed, the suppliers' names."""
    reader = reader or compat_shadow.host_reader
    moment = now or datetime.now(UTC)
    taken_at = moment.isoformat()
    with erp.connection() as db:
        db.executescript(SCHEMA)
        last = db.execute(
            "SELECT data FROM bling_payables_runs WHERE json_extract(data,'$.state')='read_ok' "
            "ORDER BY taken_at DESC LIMIT 1"
        ).fetchone()
    if last:
        previous = json.loads(last[0])
        age = moment - datetime.fromisoformat(previous["taken_at"])
        if not previous.get("details_missing") and age.total_seconds() < REST_MINUTES * 60:
            return {"taken_at": taken_at, "state": "recent"}
    try:
        listed = _list(reader)
        with erp.connection() as db:
            db.executescript(SCHEMA)
            stored = {
                r[0]: (r[1], r[2])
                for r in db.execute("SELECT id,listed,detail FROM bling_payables")
            }
            known = {r[0] for r in db.execute("SELECT id FROM bling_payable_contacts")}
        due = [
            i
            for i, row in listed.items()
            if i not in stored or stored[i][1] is None or json.loads(stored[i][0]) != row
        ][:max_details]
        details = {}
        for chunk in _batches(due):
            results = _read(reader, [(f"/contas/pagar/{i}", []) for i in chunk])
            for identifier, result in zip(chunk, results, strict=True):
                data = _data(result)
                if isinstance(data, dict):
                    details[identifier] = data
        wanted = sorted(
            {
                str((row.get("contato") or {}).get("id"))
                for row in listed.values()
                if (row.get("contato") or {}).get("id")
            }
            - known
        )[:max_details]
        names = {}
        for chunk in _batches(wanted):
            results = _read(reader, [(f"/contatos/{i}", []) for i in chunk])
            for identifier, result in zip(chunk, results, strict=True):
                data = _data(result)
                if isinstance(data, dict) and str(data.get("nome") or "").strip():
                    names[identifier] = str(data["nome"]).strip()[:200]
    except UnavailableError as error:
        return {"taken_at": taken_at, "state": str(error)}
    with erp.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        for identifier, row in listed.items():
            detail = details.get(identifier)
            previous = stored.get(identifier)
            kept = (
                json.dumps(detail, ensure_ascii=False) if detail else (previous or (None, None))[1]
            )
            if detail is None and previous and json.loads(previous[0]) != row:
                kept = None  # changed in Bling and not read again yet: shown from the list
            db.execute(
                "INSERT INTO bling_payables VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "listed=excluded.listed,detail=excluded.detail,seen_at=excluded.seen_at",
                (identifier, json.dumps(row, ensure_ascii=False), kept, taken_at),
            )
        closed = [i for i in stored if i not in listed]
        db.executemany("DELETE FROM bling_payables WHERE id=?", [(i,) for i in closed])
        db.executemany(
            "INSERT OR REPLACE INTO bling_payable_contacts VALUES (?,?)", sorted(names.items())
        )
        report = {
            "taken_at": taken_at,
            "state": "read_ok",
            "open": len(listed),
            "details_read": len(details),
            "details_missing": sum(
                1
                for r in db.execute("SELECT detail FROM bling_payables").fetchall()
                if r[0] is None
            ),
            "closed": len(closed),
            "suppliers_read": len(names),
        }
        db.execute(
            "INSERT OR REPLACE INTO bling_payables_runs VALUES (?,?)",
            (taken_at, json.dumps(report)),
        )
        db.commit()
    return report


def _money(value):
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def snapshot(db, *, today=None):
    """The copy as the Financeiro screen shows it, with totals."""
    try:
        rows = db.execute("SELECT id,listed,detail FROM bling_payables").fetchall()
        names = dict(db.execute("SELECT id,name FROM bling_payable_contacts").fetchall())
        last = db.execute(
            "SELECT data FROM bling_payables_runs WHERE json_extract(data,'$.state')='read_ok' "
            "ORDER BY taken_at DESC LIMIT 1"
        ).fetchone()
    except sqlite3.OperationalError:  # never copied yet: an empty copy, not an error
        return {
            "taken_at": None,
            "rows": [],
            "totals": {"count": 0, "amount": 0, "balance": 0, "overdue": 0},
        }
    today = today or datetime.now(UTC).date().isoformat()
    out = []
    for identifier, listed, detail in rows:
        row = {**json.loads(listed), **(json.loads(detail) if detail else {})}
        contact = str((row.get("contato") or {}).get("id") or "")
        occurrence = row.get("ocorrencia") or {}
        balance = _money(row.get("saldo", row.get("valor")))
        out.append(
            {
                "id": identifier,
                "supplier": names.get(contact) or (f"Contato {contact}" if contact else ""),
                "history": str(row.get("historico") or "")[:300],
                "document": str(row.get("numeroDocumento") or ""),
                "due_date": str(row.get("vencimento") or ""),
                "competence": str(row.get("competencia") or ""),
                "amount": _money(row.get("valor")),
                "balance": balance,
                "situation": OPEN.get(str(row.get("situacao")), str(row.get("situacao") or "")),
                "category_id": str((row.get("categoria") or {}).get("id") or "") or None,
                "payments": len(row.get("borderos") or []),
                "repeats": isinstance(occurrence, dict) and int(occurrence.get("tipo") or 1) >= 3,
                "overdue": bool(row.get("vencimento")) and str(row["vencimento"]) < today,
                "detailed": detail is not None,
            }
        )
    out.sort(key=lambda r: (r["due_date"], r["supplier"]))
    totals = {
        "count": len(out),
        "amount": round(sum(r["amount"] for r in out), 2),
        "balance": round(sum(r["balance"] for r in out), 2),
        "overdue": sum(1 for r in out if r["overdue"]),
    }
    return {
        "taken_at": json.loads(last[0])["taken_at"] if last else None,
        "rows": out,
        "totals": totals,
    }
