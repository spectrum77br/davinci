"""Bling's open payables, copied read only while Bling still keeps the money side.

The team pays its bills in Bling's Contas a pagar (171 in the November screen, with partial and
recurring payments). Until the switch the ERP only shows them: nothing is written back, and the
copy travels the same read-only way as the shadow check (DaVinci's Bling login on the server, GET
only, in DaVinci's own rate slot, never a token refresh). At the switch these are the bills that
become the ERP's own (Novos procedimentos 13 leaves that transfer for later).

Bling refuses the financial category names to DaVinci's app (403); they come from its own screen,
read on 23/11/2026 (bling_categories), and a newer category shows by its id.
Suppliers come by id, only the ones these bills point to, and only the name is kept.
"""

import json
import sqlite3
import time
from datetime import UTC, datetime
from decimal import Decimal

from app.erp_local import compat_shadow

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS bling_payables (id TEXT PRIMARY KEY, listed TEXT NOT NULL,"
    " detail TEXT, seen_at TEXT NOT NULL);"
    "CREATE TABLE IF NOT EXISTS bling_payable_contacts (id TEXT PRIMARY KEY, name TEXT NOT NULL);"
    "CREATE TABLE IF NOT EXISTS bling_payables_runs (taken_at TEXT PRIMARY KEY,"
    " data TEXT NOT NULL);"
)
# A bill's situation: 1 open, 2 paid, 3 partly paid, 4 returned, 5 cancelled. Bling numbers the list
# filter differently (checked 23/11): filter 1 brings open bills, 4 the partly paid (situation 3)
# and 3 the cancelled (situation 5).
OPEN = {"1": "Em aberto", "3": "Pago parcialmente"}
LIST_FILTERS = ("1", "4")
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


# Bling answers 429 when DaVinci and this copy ask at the same second: wait and ask again only
# what was refused.
RETRY_WAITS = (3, 8, 20)
_wait = time.sleep


def _read(reader, calls):
    results = [None] * len(calls)
    pending = list(range(len(calls)))
    for attempt in range(len(RETRY_WAITS) + 1):
        asked = [calls[i] for i in pending]
        read = reader({"calls": [{"path": path, "params": params} for path, params in asked]})
        if not compat_shadow._valid(read, len(asked)) or read.get("state") != "read_ok":
            state = read.get("state") if isinstance(read, dict) else None
            raise UnavailableError(state or "provider_unavailable")
        refused = []
        for index, result in zip(pending, read["results"], strict=True):
            if result["status"] == 429:
                refused.append(index)
            elif result["status"] not in {200, 404}:
                raise UnavailableError(f"http_{result['status']}")
            else:
                results[index] = result
        if not refused:
            return results
        if attempt == len(RETRY_WAITS):
            raise UnavailableError("http_429")
        _wait(RETRY_WAITS[attempt])
        pending = refused
    return results


def _data(result):
    body = result.get("body")
    return body.get("data") if isinstance(body, dict) else None


def _list(reader):
    listed = {}
    for situation in LIST_FILTERS:
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
                    if isinstance(row, dict) and row.get("id") and str(row.get("situacao")) in OPEN:
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
        imported = db.execute(
            "SELECT 1 FROM bling_payables_runs WHERE json_extract(data,'$.state')='imported'"
        ).fetchone()
    if imported:
        return {"taken_at": taken_at, "state": "imported"}  # the bills are the ERP's now
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
        # Partly paid bills first (their balance is only in the detail), then the nearest due.
        due = sorted(
            (
                i
                for i, row in listed.items()
                if i not in stored or stored[i][1] is None or json.loads(stored[i][0]) != row
            ),
            key=lambda i: (
                str(listed[i].get("situacao")) != "3",
                str(listed[i].get("vencimento") or ""),
            ),
        )[:max_details]
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
            "totals": {"count": 0, "amount": 0, "balance": 0, "balance_unknown": 0, "overdue": 0},
        }
    today = today or datetime.now(UTC).date().isoformat()
    out = []
    for identifier, listed, detail in rows:
        row = {**json.loads(listed), **(json.loads(detail) if detail else {})}
        contact = str((row.get("contato") or {}).get("id") or "")
        occurrence = row.get("ocorrencia") or {}
        # An open bill has no payment, so its balance is its value (140 of 140 read on 23/11); a
        # partly paid one is known only from its detail.
        if detail:
            balance = _money(row.get("saldo", row.get("valor")))
        elif str(row.get("situacao")) == "1":
            balance = _money(row.get("valor"))
        else:
            balance = None
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
        "balance": round(sum(r["balance"] for r in out if r["balance"] is not None), 2),
        "balance_unknown": sum(1 for r in out if r["balance"] is None),
        "overdue": sum(1 for r in out if r["overdue"]),
    }
    return {
        "taken_at": json.loads(last[0])["taken_at"] if last else None,
        "rows": out,
        "totals": totals,
    }


# ---------------------------------------------------------------------------------------------
# At the switch: Bling's open bills become the ERP's own (Novos procedimentos 13 leaves the moment
# for later). Bling refuses its payment records (borderôs) and category names to DaVinci's app
# (403, 23/11), so a partly paid bill comes at its full value with one payment for what Bling
# already paid, marked as before the switch (kept out of the ERP's management balance). Categories
# carry Bling's names as its screen shows them (bling_categories); one created after 23/11 shows by
# its Bling id. A DI instalment under "importado" is a stock bill of its lot, as the Importação
# files it.


def _day(value):
    text = str(value or "").strip()[:10]
    return text if len(text) == 10 and not text.startswith("0000") else ""


def _plan_rows(db):
    from app.erp_local import bling_categories, bling_compat, finance

    stock_ids = bling_compat._stock_categories()
    names = dict(db.execute("SELECT id,name FROM bling_payable_contacts").fetchall())
    plan, waiting = [], []
    for identifier, listed, detail in db.execute(
        "SELECT id,listed,detail FROM bling_payables ORDER BY id"
    ):
        if detail is None:
            waiting.append(identifier)
            continue
        row = {**json.loads(listed), **json.loads(detail)}
        value = Decimal(str(row.get("valor") or 0)).quantize(Decimal("0.01"))
        left = Decimal(str(row.get("saldo", row.get("valor")) or 0)).quantize(Decimal("0.01"))
        due = _day(row.get("vencimento"))
        issued = _day(row.get("dataEmissao")) or _day(row.get("competencia")) or due
        contact = str((row.get("contato") or {}).get("id") or "")
        category = str((row.get("categoria") or {}).get("id") or "")
        stock = category in stock_ids
        document = str(row.get("numeroDocumento") or "").strip()
        payload = {
            "kind": "payable",
            "reference": f"BLING-CP-{identifier}",
            "party": names.get(contact)
            or (f"Contato Bling {contact}" if contact else "Sem fornecedor no Bling"),
            "amount": str(value),
            "issued_on": issued,
            "competence_on": _day(row.get("competencia")) or issued,
            "due_date": due,
            "history": str(row.get("historico") or "")[:2000],
            "document_number": document[:120],
            "category": finance.STOCK_CATEGORY
            if stock
            else (
                bling_categories.path(category) or f"Bling {category}"
                if category not in {"", "0"}
                else "Sem categoria"
            ),
        }
        if stock:
            lot = document.rsplit("/", 1)[0].strip() if "/" in document else document
            payload["lot_number"] = lot or f"Bling {identifier}"
        paid = value - left if Decimal(0) < left < value else Decimal(0)
        plan.append({"id": identifier, "payload": payload, "paid": paid, "left": left})
    return plan, waiting


def import_preview(db):
    """What the switch would bring in, without writing anything."""
    try:
        plan, waiting = _plan_rows(db)
        done = db.execute(
            "SELECT data FROM bling_payables_runs WHERE json_extract(data,'$.state')='imported'"
        ).fetchone()
    except sqlite3.OperationalError:
        return {"bills": 0, "waiting": 0, "imported": None}
    by_category = {}
    for item in plan:
        name = item["payload"]["category"]
        by_category[name] = by_category.get(name, 0) + 1
    return {
        "bills": len(plan),
        "waiting": len(waiting),
        "amount": float(sum(Decimal(i["payload"]["amount"]) for i in plan)),
        "paid_before": float(sum(i["paid"] for i in plan)),
        "to_pay": float(sum(i["left"] for i in plan)),
        "stock_lots": len(
            {i["payload"]["lot_number"] for i in plan if "lot_number" in i["payload"]}
        ),
        "unknown_supplier": sum(
            1 for i in plan if i["payload"]["party"].startswith(("Contato Bling", "Sem fornecedor"))
        ),
        "categories": dict(sorted(by_category.items(), key=lambda kv: -kv[1])[:12]),
        "imported": json.loads(done[0]) if done else None,
    }


def import_all(erp, *, now=None):
    """The switch: every copied bill becomes an ERP payable, once. Refused while details are
    still being read, so nothing comes half."""
    from app.erp_local.core import ERPError

    with erp.connection() as db:
        db.executescript(SCHEMA)
        plan, waiting = _plan_rows(db)
    if waiting:
        raise ERPError(
            "bling_payables_reading",
            f"Ainda lendo o detalhe de {len(waiting)} contas do Bling. "
            "Tente depois da próxima rodada.",
            409,
        )
    created = paid = 0
    for item in plan:
        result = erp.command(
            f"bling-cp-{item['id']}",
            "finance.entry.create",
            item["payload"],
            actor="importação do Bling",
        )
        fresh = not (result.get("duplicate") or result.get("replayed"))
        created += 1 if fresh else 0
        if item["paid"] and fresh:
            with erp.connection() as db:
                document = (
                    result.get("ids", [None])[0]
                    or db.execute(
                        "SELECT id FROM documents WHERE external_key=?",
                        (
                            json.dumps(["manual_finance", "payable", item["payload"]["reference"]])
                            + ":1",
                        ),
                    ).fetchone()[0]
                )
                installment = db.execute(
                    "SELECT i.id FROM accounts a JOIN finance_installments i ON i.account_id=a.id "
                    "WHERE a.document_id=? AND i.active=1 ORDER BY i.due_date LIMIT 1",
                    (document,),
                ).fetchone()[0]
            erp.command(
                f"bling-cp-pago-{item['id']}",
                "finance.pay",
                {
                    "installment_id": installment,
                    "amount": str(item["paid"]),
                    "external_id": f"BLING-PAGO-{item['id']}",
                    "date": item["payload"]["competence_on"] or item["payload"]["due_date"],
                    "before_switch": True,
                },
                actor="importação do Bling",
            )
            paid += 1
    report = {
        "taken_at": (now or datetime.now(UTC)).isoformat(),
        "state": "imported",
        "bills": len(plan),
        "created": created,
        "payments_before_switch": paid,
    }
    with erp.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            "INSERT OR REPLACE INTO bling_payables_runs VALUES (?,?)",
            (report["taken_at"], json.dumps(report)),
        )
        db.commit()
    return report
