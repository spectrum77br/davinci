"""Local financial ledger. Records cash facts; never contacts a bank."""

import hashlib
import json
import re
from calendar import monthrange
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.erp_local.core import ERPError, dumps, number, text

SCHEMA = """
CREATE TABLE IF NOT EXISTS finance_installments (
 id TEXT PRIMARY KEY, account_id TEXT REFERENCES accounts(id), due_date TEXT NOT NULL,
 amount TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS finance_banks (id TEXT PRIMARY KEY, name TEXT, opening TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS finance_payments (
 id TEXT PRIMARY KEY, installment_id TEXT REFERENCES finance_installments(id),
 bank_id TEXT REFERENCES finance_banks(id), external_key TEXT UNIQUE NOT NULL,
 principal TEXT NOT NULL, cash TEXT NOT NULL, data TEXT NOT NULL,
 reversed INTEGER NOT NULL DEFAULT 0,
 operation_key TEXT REFERENCES events(operation_key), created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS finance_matches (
 id TEXT PRIMARY KEY, bank_id TEXT REFERENCES finance_banks(id), statement_line TEXT NOT NULL,
 payment_id TEXT UNIQUE REFERENCES finance_payments(id), amount TEXT NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE(bank_id,statement_line));
"""
COMMANDS = {
    "finance.entry.create",
    "finance.entry.cancel",
    "bank.create",
    "finance.schedule",
    "finance.pay",
    "finance.reverse",
    "finance.reconcile",
    "finance.unreconcile",
    "account.settle",
}

ENTRY_FIELDS = {
    "cost_centers",
    "lot_number",
    "lot_value",
    "exchange_rate",
    "kind",
    "reference",
    "party",
    "history",
    "amount",
    "issued_on",
    "competence_on",
    "due_date",
    "payment_method",
    "category",
    "document_number",
    "occurrence",
    "count",
    # As Bling's account form: bank slip line and tax guide data, kept for paying it later.
    "barcode",
    "tax_code",
    "tax_period",
    # Bling's "Conta financeira": the bank account or cash this entry is paid from or into.
    "bank_id",
}
MONTH_INTERVALS = {"monthly": 1, "bimonthly": 2, "quarterly": 3, "semiannual": 6, "annual": 12}


def entry_plan(amount, due_date, occurrence, count):
    """Bounded occurrences; installment total differs from a repeated charge.

    Month-end dates always derive from the original day (Jan 31 -> Feb 28 -> Mar 31).
    No holiday adjustment or perpetual scheduler is assumed from the source UI.
    """
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 120:
        raise ERPError("entry_count", "Informe de 1 a 120 ocorrências.")
    if occurrence not in {"once", "installments", "weekly", "fortnightly", *MONTH_INTERVALS}:
        raise ERPError("entry_occurrence", "Ocorrência financeira não reconhecida.")
    if occurrence == "once" and count != 1:
        raise ERPError("entry_count", "Uma conta única deve ter uma ocorrência.")
    first = date.fromisoformat(due_date)
    cents = int(amount * 100)
    base, remainder = divmod(cents, count) if occurrence == "installments" else (cents, 0)
    if base == 0:
        raise ERPError("entry_amount", "Cada parcela deve ter ao menos um centavo.")
    result = []
    for index in range(count):
        try:
            if occurrence in {"weekly", "fortnightly"}:
                due = first + timedelta(days=index * (7 if occurrence == "weekly" else 15))
            else:
                month = first.year * 12 + first.month - 1
                month += index * MONTH_INTERVALS.get(occurrence, 1)
                year, month = divmod(month, 12)
                due = date(year, month + 1, min(first.day, monthrange(year, month + 1)[1]))
        except (ValueError, OverflowError):
            raise ERPError("invalid_date", "O plano ultrapassa o calendário suportado.") from None
        value = Decimal(base + (index < remainder)) / 100
        result.append({"due_date": due.isoformat(), "amount": f"{value:.2f}"})
    return result


COST_CENTERS = {"celular": "Celular", "mala": "Mala", "eletro": "Eletro"}
# Novos procedimentos 13, item 6: stock is not an expense; it closes by lot.
STOCK_CATEGORY = "Estoque"


def _management(p):
    """Novos procedimentos 13: a payable split by % among the three departments (cost centers),
    and a stock lot with its value and exchange rate, so the lot closes with its difference."""
    out = {}
    centers = p.get("cost_centers")
    if isinstance(centers, str) and centers.strip():
        try:
            centers = json.loads(centers)
        except json.JSONDecodeError:
            raise ERPError("entry_cost_centers", "Rateio por centro de custo inválido.") from None
    if centers:
        if not isinstance(centers, dict) or set(centers) - set(COST_CENTERS):
            raise ERPError("entry_cost_centers", "Os centros de custo são Celular, Mala e Eletro.")
        split = {k: number(v, zero=True) for k, v in centers.items() if str(v).strip()}
        if sum(split.values()) != 100:
            raise ERPError("entry_cost_centers", "O rateio entre os centros de custo soma 100%.")
        out["cost_centers"] = {k: str(v) for k, v in split.items() if v}
    if p.get("lot_number"):
        out["lot_number"] = text(p["lot_number"], "número do lote", 60)
        if p.get("lot_value") not in (None, ""):
            out["lot_value"] = str(number(p["lot_value"], money=True))
        if p.get("exchange_rate") not in (None, ""):
            out["exchange_rate"] = str(number(p["exchange_rate"], unit=True))
    return out


def management(db):
    """Novos procedimentos 13, item 1: in the management balance a bill counts on the day it was
    paid, split among the cost centers. What is still open shows by its due date as a forecast.
    Stock is not an expense (item 6): it stays in the lots."""
    rows = {}

    def add(month, category, centers, field, amount):
        for center, share in (centers or {"": "100"}).items():
            entry = rows.setdefault(
                (month, category, center),
                {
                    "month": month,
                    "category": category,
                    "cost_center": COST_CENTERS.get(center, "Sem rateio"),
                    "paid": Decimal(0),
                    "forecast": Decimal(0),
                },
            )
            entry[field] += amount * Decimal(share) / 100

    for row in db.execute(
        "SELECT d.data,a.id FROM documents d JOIN accounts a ON a.document_id=d.id "
        "WHERE d.kind='financial' AND a.kind='payable' AND a.status!='cancelled'"
    ):
        data = json.loads(row[0])
        category = (data.get("category") or "").strip() or "Sem categoria"
        if category.casefold() == STOCK_CATEGORY.casefold():
            continue
        centers = data.get("cost_centers") or {}
        for installment in db.execute(
            "SELECT id,due_date,amount FROM finance_installments WHERE account_id=? AND active=1",
            (row[1],),
        ):
            settled = Decimal(0)
            for payment in db.execute(
                "SELECT principal,data FROM finance_payments WHERE installment_id=? AND reversed=0",
                (installment[0],),
            ):
                when = str(json.loads(payment[1]).get("date") or "")[:7]
                add(when, category, centers, "paid", Decimal(payment[0]))
                settled += Decimal(payment[0])
            left = Decimal(installment[2]) - settled
            if left > 0:
                add(str(installment[1])[:7], category, centers, "forecast", left)
    cent = Decimal("0.01")
    return [
        {**e, "paid": str(e["paid"].quantize(cent)), "forecast": str(e["forecast"].quantize(cent))}
        for _, e in sorted(rows.items())
    ]


def lots(db):
    """Each stock lot: its value, what its payables add up to and what was paid. Paid above the
    lot value is a negative difference in the summary; below it, positive."""
    out = {}
    for row in db.execute(
        "SELECT d.data,a.id,a.status FROM documents d JOIN accounts a ON a.document_id=d.id "
        "WHERE d.kind='financial' AND a.kind='payable' AND a.status!='cancelled'"
    ):
        data = json.loads(row[0])
        lot = data.get("lot_number")
        if not lot:
            continue
        entry = out.setdefault(
            lot,
            {
                "lot": lot,
                "value": None,
                "exchange_rate": None,
                "payables": Decimal(0),
                "paid": Decimal(0),
                "open": 0,
            },
        )
        if data.get("lot_value"):
            entry["value"] = Decimal(data["lot_value"])
        if data.get("exchange_rate"):
            entry["exchange_rate"] = data["exchange_rate"]
        entry["payables"] += Decimal(data["amount"])
        for installment in db.execute(
            "SELECT id FROM finance_installments WHERE account_id=?", (row[1],)
        ):
            entry["paid"] += paid(db, installment[0])
        entry["open"] += row[2] == "open"
    result = []
    for entry in out.values():
        difference = entry["value"] - entry["paid"] if entry["value"] is not None else None
        result.append(
            {
                **entry,
                "value": str(entry["value"]) if entry["value"] is not None else None,
                "payables": str(entry["payables"]),
                "paid": str(entry["paid"]),
                "difference": str(difference) if difference is not None else None,
                "closed": entry["open"] == 0,
            }
        )
    return sorted(result, key=lambda e: e["lot"])


def create_entry(erp, db, p):
    if p.get("kind") not in {"payable", "receivable"}:
        raise ERPError("entry_kind", "Escolha conta a pagar ou a receber.")
    value = number(p.get("amount"), money=True)
    required = {k: text(p.get(k), k) for k in ("reference", "party")}
    metadata = {
        k: text(p.get(k), k, 2000)
        if k == "history" and p.get(k)
        else text(p.get(k), k)
        if p.get(k)
        else ""
        for k in ("history", "payment_method", "category", "document_number")
    }
    barcode = re.sub(r"\D", "", str(p.get("barcode") or ""))
    if barcode and len(barcode) not in {44, 47, 48}:
        raise ERPError("entry_barcode", "A linha digitável tem 44, 47 ou 48 números.")
    if barcode:
        metadata["barcode"] = barcode
    for k in ("tax_code", "tax_period"):
        if p.get(k):
            metadata[k] = text(p.get(k), k, 40)
    if p.get("bank_id"):
        bank = text(p["bank_id"], "conta financeira")
        if not db.execute("SELECT 1 FROM finance_banks WHERE id=?", (bank,)).fetchone():
            raise ERPError("entry_bank", "Conta financeira não encontrada.", 404)
        metadata["bank_id"] = bank
    metadata.update(_management(p))
    if (
        p["kind"] == "payable"
        and metadata.get("category", "").strip().casefold() == STOCK_CATEGORY.casefold()
        and not metadata.get("lot_number")
    ):
        raise ERPError("entry_lot", "Conta de Estoque precisa do número do lote.")
    dates = {k: day(p.get(k)) for k in ("issued_on", "competence_on", "due_date")}
    occurrence, count = p.get("occurrence", "once"), p.get("count", 1)
    plan = entry_plan(value, dates["due_date"], occurrence, count)
    data = {
        "kind": p["kind"],
        **required,
        **metadata,
        **dates,
        "amount": str(value),
        "occurrence": occurrence,
        "count": count,
    }
    fingerprint = hashlib.sha256(dumps(data).encode()).hexdigest()
    prefix = dumps(["manual_finance", p["kind"], required["reference"]])
    previous = db.execute(
        "SELECT id,data FROM documents WHERE external_key=?", (prefix + ":1",)
    ).fetchone()
    if previous:
        existing = json.loads(previous["data"])
        if existing["series_fingerprint"] != fingerprint:
            raise ERPError("entry_reference_conflict", "Referência já usada com outros dados.", 409)
        return {"series_id": existing["series_id"], "duplicate": True, "external_writes": False}
    series = str(uuid4())
    identifiers = []
    for index, installment in enumerate(plan, 1):
        identifier = str(uuid4())
        document = {
            **data,
            **installment,
            "series_id": series,
            "sequence": index,
            "series_fingerprint": fingerprint,
            "created_at": erp.now(),
        }
        db.execute(
            "INSERT INTO documents VALUES (?,?,?,?,?)",
            (identifier, "financial", prefix + f":{index}", "open", dumps(document)),
        )
        account_id = str(uuid4())
        db.execute(
            "INSERT INTO accounts VALUES (?,?,?,?,?)",
            (account_id, identifier, p["kind"], installment["amount"], "open"),
        )
        schedule(db, account_id, [installment])
        identifiers.append(identifier)
    return {"ids": identifiers, "series_id": series, "duplicate": False, "external_writes": False}


def cancel_entry(erp, db, p):
    document, data = erp.document(db, p.get("id"), "financial")
    header = db.execute("SELECT * FROM accounts WHERE document_id=?", (document["id"],)).fetchone()
    if any(
        paid(db, r[0])
        for r in db.execute(
            "SELECT id FROM finance_installments WHERE account_id=?", (header["id"],)
        )
    ):
        raise ERPError("entry_paid", "Estorne as baixas antes de cancelar esta conta.", 409)
    db.execute("UPDATE accounts SET status='cancelled' WHERE id=?", (header["id"],))
    erp.update_document(db, document["id"], "cancelled", data)
    return {"id": document["id"], "status": "cancelled", "external_writes": False}


def today():
    return datetime.now(ZoneInfo("America/Sao_Paulo")).date().isoformat()


def day(value):
    try:
        return date.fromisoformat(value).isoformat()
    except (ValueError, TypeError):
        raise ERPError("invalid_date", "Informe uma data válida.") from None


def account(db, identifier):
    row = db.execute("SELECT * FROM accounts WHERE id=?", (identifier,)).fetchone()
    if not row:
        raise ERPError("account_not_found", "Conta não encontrada.", 404)
    return row


def paid(db, installment):
    return sum(
        (
            Decimal(r[0])
            for r in db.execute(
                "SELECT principal FROM finance_payments WHERE installment_id=? AND reversed=0",
                (installment,),
            )
        ),
        Decimal(0),
    )


def schedule(db, identifier, raw):
    header = account(db, identifier)
    if header["status"] == "cancelled":
        raise ERPError("cancelled_account", "Conta cancelada não recebe parcelas.", 409)
    if not isinstance(raw, list) or not 1 <= len(raw) <= 120:
        raise ERPError("invalid_schedule", "Informe de 1 a 120 parcelas.")
    plan = []
    for row in raw:
        if not isinstance(row, dict) or set(row) != {"due_date", "amount"}:
            raise ERPError("invalid_schedule", "Campos da parcela inválidos.")
        plan.append(
            {"due_date": day(row["due_date"]), "amount": str(number(row["amount"], money=True))}
        )
    if sum((Decimal(p["amount"]) for p in plan), Decimal(0)) != Decimal(header["amount"]):
        raise ERPError("schedule_total", "A soma das parcelas deve ser igual ao valor da conta.")
    existing = db.execute(
        "SELECT * FROM finance_installments WHERE account_id=? AND active=1 ORDER BY rowid",
        (identifier,),
    ).fetchall()
    if [{k: r[k] for k in ("due_date", "amount")} for r in existing] == plan:
        return
    if any(paid(db, r["id"]) for r in existing):
        raise ERPError("schedule_paid", "Estorne as baixas antes de alterar as parcelas.", 409)
    db.execute("UPDATE finance_installments SET active=0 WHERE account_id=?", (identifier,))
    db.executemany(
        "INSERT INTO finance_installments VALUES (?,?,?,?,1)",
        [(str(uuid4()), identifier, p["due_date"], p["amount"]) for p in plan],
    )


def ensure_schedules(db):
    for header in db.execute(
        "SELECT a.* FROM accounts a WHERE a.status!='cancelled' AND NOT EXISTS "
        "(SELECT 1 FROM finance_installments i WHERE i.account_id=a.id AND i.active=1)"
    ).fetchall():
        if Decimal(header["amount"]) == 0:
            continue
        doc = db.execute(
            "SELECT data FROM documents WHERE id=?", (header["document_id"],)
        ).fetchone()
        plan = (
            json.loads(doc[0]).get("installments")
            if doc and header["kind"] not in {"commission", "refund"}
            else None
        )
        schedule(db, header["id"], plan or [{"due_date": today(), "amount": header["amount"]}])
        # An old paid account predating this ledger is an opening fact, not a new payment.
        if header["status"] == "paid":
            installments = db.execute(
                "SELECT id,amount FROM finance_installments WHERE account_id=? AND active=1",
                (header["id"],),
            ).fetchall()
            for installment in installments:
                db.execute(
                    "INSERT INTO finance_payments VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        str(uuid4()),
                        installment["id"],
                        None,
                        "opening-" + installment["id"],
                        installment["amount"],
                        installment["amount"],
                        dumps({"basis": "legacy_paid_opening"}),
                        0,
                        None,
                        datetime.now(UTC).isoformat(),
                    ),
                )


def refresh_header(db, identifier):
    header = account(db, identifier)
    if header["status"] == "cancelled":
        return
    amount = sum(
        (
            paid(db, r[0])
            for r in db.execute(
                "SELECT id FROM finance_installments WHERE account_id=? AND active=1", (identifier,)
            )
        ),
        Decimal(0),
    )
    status = "paid" if amount == Decimal(header["amount"]) else "partial" if amount else "open"
    db.execute("UPDATE accounts SET status=? WHERE id=?", (status, identifier))
    db.execute(
        "UPDATE documents SET status=? WHERE id=? AND kind='financial'",
        (status, header["document_id"]),
    )


def post_payment(erp, db, key, p):
    installment = db.execute(
        "SELECT * FROM finance_installments WHERE id=? AND active=1", (p.get("installment_id"),)
    ).fetchone()
    if not installment:
        raise ERPError("installment_not_found", "Parcela não encontrada.", 404)
    header = account(db, installment["account_id"])
    if header["status"] == "cancelled":
        raise ERPError("cancelled_account", "Uma conta cancelada não pode receber baixa.", 409)
    principal = number(p.get("amount"), money=True)
    extras = {
        n: number(p.get(n, "0"), money=True, zero=True) for n in ("fee", "discount", "interest")
    }
    bank = p.get("bank_id") or None
    if bank and not db.execute("SELECT 1 FROM finance_banks WHERE id=?", (bank,)).fetchone():
        raise ERPError("bank_not_found", "Conta financeira não encontrada.", 404)
    cash = principal + extras["interest"] - extras["discount"]
    cash += extras["fee"] * (-1 if header["kind"] == "receivable" else 1)
    if cash < 0:
        raise ERPError("negative_cash", "Taxa e desconto não podem tornar o caixa negativo.")
    data = {
        "date": day(p.get("date", today())),
        "amount": str(principal),
        **{n: str(v) for n, v in extras.items()},
        "installment_id": installment["id"],
        "bank_id": bank,
    }
    external_key = dumps([bank, text(p.get("external_id"), "comprovante")])
    from app.erp_local.pos import receipt_guard

    receipt_guard(db, bank, p["external_id"].strip())
    previous = db.execute(
        "SELECT * FROM finance_payments WHERE external_key=?", (external_key,)
    ).fetchone()
    if previous:
        if previous["data"] != dumps(data):
            raise ERPError("payment_conflict", "Comprovante já usado com outros dados.", 409)
        return {"id": previous["id"], "duplicate": True}
    remaining = Decimal(installment["amount"]) - paid(db, installment["id"])
    if principal > remaining:
        raise ERPError("overpayment", "A baixa ultrapassa o saldo da parcela.", 409)
    if header["kind"] == "commission":
        from app.erp_local.sellers import assert_payable

        assert_payable(db, header["document_id"], principal)
    identifier = str(uuid4())
    db.execute(
        "INSERT INTO finance_payments VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            identifier,
            installment["id"],
            bank,
            external_key,
            str(principal),
            str(cash),
            dumps(data),
            0,
            key,
            erp.now(),
        ),
    )
    refresh_header(db, header["id"])
    return {"id": identifier, "cash": str(cash), "duplicate": False}


def apply(erp, db, key, kind, p):
    fields = {
        "finance.entry.create": ENTRY_FIELDS,
        "finance.entry.cancel": {"id"},
        "bank.create": {"id", "name", "opening"},
        "finance.schedule": {"id", "installments"},
        "finance.pay": {
            "installment_id",
            "amount",
            "fee",
            "discount",
            "interest",
            "bank_id",
            "external_id",
            "date",
        },
        "finance.reverse": {"id"},
        "finance.reconcile": {"id", "statement_line", "amount"},
        "finance.unreconcile": {"id"},
        "account.settle": {"id"},
    }
    if set(p) - fields[kind]:
        raise ERPError("unsupported_fields", "Campos financeiros não suportados.")
    ensure_schedules(db)
    if kind == "finance.entry.create":
        return create_entry(erp, db, p)
    if kind == "finance.entry.cancel":
        return cancel_entry(erp, db, p)
    if kind == "bank.create":
        identifier, name = text(p.get("id"), "identificador"), text(p.get("name"), "nome")
        if db.execute("SELECT 1 FROM finance_banks WHERE id=?", (identifier,)).fetchone():
            raise ERPError("duplicate_bank", "Conta financeira já cadastrada.", 409)
        opening = number(p.get("opening", "0"), money=True, zero=True)
        db.execute("INSERT INTO finance_banks VALUES (?,?,?)", (identifier, name, str(opening)))
        return {"id": identifier}
    if kind == "finance.schedule":
        schedule(db, p.get("id"), p.get("installments"))
        return {"id": p["id"]}
    if kind == "finance.pay":
        return post_payment(erp, db, key, p)
    if kind == "finance.unreconcile":
        match = db.execute("SELECT * FROM finance_matches WHERE id=?", (p.get("id"),)).fetchone()
        if not match:
            raise ERPError("match_not_found", "Conciliação não encontrada.", 404)
        # The original association and its reversal remain in the append-only command events.
        db.execute("DELETE FROM finance_matches WHERE id=?", (match["id"],))
        return {"id": match["id"], "previous": dict(match)}
    if kind == "account.settle":
        header = account(db, p.get("id"))
        if header["status"] == "cancelled":
            raise ERPError("cancelled_account", "Uma conta cancelada não pode receber baixa.", 409)
        for row in db.execute(
            "SELECT * FROM finance_installments WHERE account_id=? AND active=1", (header["id"],)
        ).fetchall():
            remaining = Decimal(row["amount"]) - paid(db, row["id"])
            if remaining:
                post_payment(
                    erp,
                    db,
                    key,
                    {
                        "installment_id": row["id"],
                        "amount": str(remaining),
                        "external_id": hashlib.sha256(dumps([key, row["id"]]).encode()).hexdigest(),
                    },
                )
        if Decimal(header["amount"]) == 0:
            db.execute("UPDATE accounts SET status='paid' WHERE id=?", (header["id"],))
        return {"id": header["id"], "status": "paid"}
    payment = db.execute(
        "SELECT p.*,i.account_id,a.kind FROM finance_payments p "
        "JOIN finance_installments i ON i.id=p.installment_id "
        "JOIN accounts a ON a.id=i.account_id WHERE p.id=?",
        (p.get("id"),),
    ).fetchone()
    if not payment:
        raise ERPError("payment_not_found", "Baixa não encontrada.", 404)
    if kind == "finance.reverse":
        if db.execute(
            "SELECT 1 FROM cash_refunds WHERE payment_id=? AND reversed=0", (payment["id"],)
        ).fetchone():
            raise ERPError(
                "cash_refund_review",
                "Desfaça a classificação do reembolso no caixa "
                "antes de estornar a baixa financeira.",
                409,
            )
        if json.loads(payment["data"]).get("basis") == "legacy_paid_opening":
            raise ERPError(
                "opening_finance_review",
                "A baixa de abertura exige conciliação do histórico antes de estornar.",
                409,
            )
        header = account(db, payment["account_id"])
        order = db.execute(
            "SELECT status FROM documents WHERE id=? AND kind='order'", (header["document_id"],)
        ).fetchone()
        from app.erp_local.returns import active_refund

        if header["kind"] == "receivable" and active_refund(db, header["document_id"]):
            raise ERPError(
                "return_finance_review",
                "Concilie os reembolsos aprovados antes de estornar o recebimento.",
                409,
            )
        if (
            header["kind"] == "receivable"
            and order
            and order["status"] in {"returned", "cancelled"}
        ):
            raise ERPError(
                "return_finance_review",
                "Concilie o reembolso da devolução antes de estornar o recebimento da venda.",
                409,
            )
        if db.execute(
            "SELECT 1 FROM finance_matches WHERE payment_id=?", (payment["id"],)
        ).fetchone():
            raise ERPError("matched_payment", "Desfaça a conciliação antes do estorno.", 409)
        db.execute("UPDATE finance_payments SET reversed=1 WHERE id=?", (payment["id"],))
        refresh_header(db, payment["account_id"])
        return {"id": payment["id"], "reversed": True}
    if not payment["bank_id"] or payment["reversed"]:
        raise ERPError(
            "reconcile_payment", "A conciliação exige uma baixa ativa com conta financeira.", 409
        )
    expected = Decimal(payment["cash"]) * (1 if payment["kind"] == "receivable" else -1)
    try:
        actual = Decimal(str(p.get("amount")))
    except Exception:
        raise ERPError(
            "reconcile_amount", "Informe o valor exato e com sinal do extrato."
        ) from None
    if not actual.is_finite() or actual != expected:
        raise ERPError("reconcile_amount", "O valor do extrato difere do caixa da baixa.", 409)
    line = text(p.get("statement_line"), "linha do extrato")
    existing = db.execute(
        "SELECT * FROM finance_matches WHERE payment_id=? OR (bank_id=? AND statement_line=?)",
        (payment["id"], payment["bank_id"], line),
    ).fetchone()
    if existing:
        if existing["payment_id"] != payment["id"] or existing["statement_line"] != line:
            raise ERPError("reconcile_conflict", "Linha do extrato já conciliada.", 409)
        return {"id": existing["id"], "duplicate": True}
    identifier = str(uuid4())
    db.execute(
        "INSERT INTO finance_matches VALUES (?,?,?,?,?,?)",
        (identifier, payment["bank_id"], line, payment["id"], str(expected), erp.now()),
    )
    return {"id": identifier, "duplicate": False}


def snapshot(db):
    installments = []
    for row in db.execute(
        "SELECT i.*,a.kind,a.status AS account_status FROM finance_installments i "
        "JOIN accounts a ON a.id=i.account_id WHERE i.active=1 ORDER BY i.due_date,i.rowid"
    ):
        done = paid(db, row["id"])
        extra = {}
        if row["kind"] == "commission":
            from app.erp_local.sellers import entitlement

            header = account(db, row["account_id"])
            release = entitlement(db, header["document_id"])
            extra["released_remaining"] = release["available_to_pay"]
        installments.append(
            {
                **dict(row),
                **extra,
                "paid": str(done),
                "remaining": str(Decimal(row["amount"]) - done),
            }
        )
    banks = []
    for bank in db.execute("SELECT * FROM finance_banks ORDER BY name"):
        changes = sum(
            (
                Decimal(r[0]) * (1 if r[1] == "receivable" else -1)
                for r in db.execute(
                    "SELECT p.cash,a.kind FROM finance_payments p "
                    "JOIN finance_installments i ON i.id=p.installment_id "
                    "JOIN accounts a ON a.id=i.account_id WHERE p.bank_id=? AND p.reversed=0",
                    (bank["id"],),
                )
            ),
            Decimal(0),
        )
        banks.append({**dict(bank), "balance": str(Decimal(bank["opening"]) + changes)})
    return {
        "manual_documents": [
            {**dict(r), "data": json.loads(r["data"])}
            for r in db.execute(
                "SELECT * FROM documents WHERE kind='financial' ORDER BY rowid DESC"
            )
        ],
        "banks": banks,
        "installments": installments,
        "payments": [
            dict(r)
            for r in db.execute("SELECT * FROM finance_payments ORDER BY rowid DESC LIMIT 200")
        ],
        "matches": [
            dict(r)
            for r in db.execute("SELECT * FROM finance_matches ORDER BY rowid DESC LIMIT 200")
        ],
        "external_payments": False,
    }
