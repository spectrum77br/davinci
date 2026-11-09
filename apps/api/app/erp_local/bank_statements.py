"""Bank statements imported from the file the bank gives (OFX or CSV), one line per movement.

Importing never settles anything. A suggestion needs the exact signed amount, the same account and
a close date, and only exists when it fits a single payment; a person confirms it.
"""

import csv
import hashlib
import io
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from app.erp_local.core import ERPError, dumps, text

SCHEMA = """
CREATE TABLE IF NOT EXISTS finance_statement_lines (
 bank_id TEXT NOT NULL REFERENCES finance_banks(id), line_id TEXT NOT NULL, posted_on TEXT NOT NULL,
 amount TEXT NOT NULL, memo TEXT NOT NULL, document TEXT NOT NULL, imported_at TEXT NOT NULL,
 PRIMARY KEY(bank_id,line_id));
"""
COMMANDS = {"bank.statement.import"}
MAX_LINES = 5000
NEAR_DAYS = 5
OFX_FIELD = re.compile(r"<(\w+)>([^<\r\n]*)")


def _amount(raw):
    value = str(raw).strip().replace("R$", "").replace(" ", "")
    if "," in value:
        value = value.replace(".", "").replace(",", ".")
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise ERPError("statement_amount", "Valor inválido no extrato.") from None
    if not number.is_finite() or number != number.quantize(Decimal("0.01")):
        raise ERPError("statement_amount", "Valor inválido no extrato.")
    return str(number.quantize(Decimal("0.01")))


def _day(raw):
    value = str(raw).strip()
    for pattern, size in (("%Y%m%d", 8), ("%Y-%m-%d", 10), ("%d/%m/%Y", 10)):
        try:
            return datetime.strptime(value[:size], pattern).date().isoformat()  # noqa: DTZ007
        except ValueError:
            continue
    raise ERPError("statement_date", "Data inválida no extrato.")


def parse_ofx(content):
    lines = []
    for block in re.findall(
        r"<STMTTRN>(.*?)(?:</STMTTRN>|(?=<STMTTRN>)|</BANKTRANLIST>)", content, re.S
    ):
        fields = {tag.upper(): value.strip() for tag, value in OFX_FIELD.findall(block)}
        if not {"DTPOSTED", "TRNAMT"} <= set(fields):
            raise ERPError("statement_format", "Lançamento do OFX sem data ou valor.")
        posted, amount = _day(fields["DTPOSTED"]), _amount(fields["TRNAMT"])
        memo = fields.get("MEMO") or fields.get("NAME") or ""
        lines.append(
            {
                "line_id": fields.get("FITID") or _digest(posted, amount, memo, len(lines)),
                "posted_on": posted,
                "amount": amount,
                "memo": memo[:200],
                "document": fields.get("CHECKNUM", "")[:60],
            }
        )
    return lines


def _digest(*parts):
    return hashlib.sha256(dumps([str(p) for p in parts]).encode()).hexdigest()[:32]


def parse_csv(content):
    """Date, description and amount, in the columns the file names or in that order."""
    sample = content[:2000]
    delimiter = ";" if sample.count(";") >= sample.count(",") else ","
    rows = [
        r
        for r in csv.reader(io.StringIO(content), delimiter=delimiter)
        if any(c.strip() for c in r)
    ]
    if not rows:
        raise ERPError("statement_format", "Arquivo de extrato vazio.")
    header = [c.strip().casefold() for c in rows[0]]
    names = {"data": "date", "date": "date", "valor": "amount", "amount": "amount"}
    found = {names[c]: i for i, c in enumerate(header) if c in names}
    memo_at = next(
        (
            i
            for i, c in enumerate(header)
            if c in {"descrição", "descricao", "histórico", "historico", "memo"}
        ),
        None,
    )
    if {"date", "amount"} <= set(found):
        body = rows[1:]
    else:
        found, memo_at, body = {"date": 0, "amount": 2}, 1, rows
    result = []
    for index, row in enumerate(body):
        posted, amount = _day(row[found["date"]]), _amount(row[found["amount"]])
        memo = row[memo_at].strip() if memo_at is not None and memo_at < len(row) else ""
        result.append(
            {
                "line_id": _digest(posted, amount, memo, index),
                "posted_on": posted,
                "amount": amount,
                "memo": memo[:200],
                "document": "",
            }
        )
    return result


def parse(content):
    lines = parse_ofx(content) if "<STMTTRN>" in content.upper() else parse_csv(content)
    if not lines or len(lines) > MAX_LINES:
        raise ERPError("statement_format", f"O extrato deve ter de 1 a {MAX_LINES} lançamentos.")
    return lines


def apply(erp, db, p):
    if (
        not isinstance(p, dict)
        or set(p) != {"bank_id", "content"}
        or not isinstance(p["content"], str)
    ):
        raise ERPError("statement_fields", "Informe a conta e o arquivo do extrato.")
    bank = text(p["bank_id"], "conta financeira")
    if not db.execute("SELECT 1 FROM finance_banks WHERE id=?", (bank,)).fetchone():
        raise ERPError("statement_bank", "Conta financeira não encontrada.", 404)
    added = repeated = 0
    for line in parse(p["content"]):
        done = db.execute(
            "INSERT OR IGNORE INTO finance_statement_lines VALUES (?,?,?,?,?,?,?)",
            (
                bank,
                line["line_id"],
                line["posted_on"],
                line["amount"],
                line["memo"],
                line["document"],
                erp.now(),
            ),
        )
        added += done.rowcount
        repeated += 1 - done.rowcount
    return {"added": added, "repeated": repeated}


def snapshot(db):
    """Lines still open at the top, each with the one payment it could be, if there is just one."""
    matched = {
        (r["bank_id"], r["statement_line"]) for r in db.execute("SELECT * FROM finance_matches")
    }
    candidates = {}
    for row in _open_payments(db):
        sign = 1 if row["kind"] == "receivable" else -1
        signed = Decimal(str(row["cash"])) * sign
        candidates.setdefault((row["bank_id"], str(signed)), []).append(row)
    lines = []
    for row in db.execute(
        "SELECT * FROM finance_statement_lines ORDER BY posted_on DESC,line_id LIMIT 500"
    ):
        is_matched = (row["bank_id"], row["line_id"]) in matched
        near = [
            c
            for c in candidates.get((row["bank_id"], row["amount"]), [])
            if _close(c, row["posted_on"])
        ]
        lines.append(
            {
                "bank_id": row["bank_id"],
                "line_id": row["line_id"],
                "posted_on": row["posted_on"],
                "amount": row["amount"],
                "memo": row["memo"],
                "document": row["document"],
                "matched": is_matched,
                "suggestion": near[0]["id"] if not is_matched and len(near) == 1 else None,
            }
        )
    lines.sort(key=lambda line: line["matched"])
    return {"lines": lines}


def _open_payments(db):
    """Every active settlement with an account, not only the ones the screen happens to list."""
    return [
        dict(row)
        for row in db.execute(
            "SELECT p.id,p.bank_id,p.cash,p.created_at,p.reversed,a.kind,"
            "json_extract(p.data,'$.date') AS date FROM finance_payments p "
            "JOIN finance_installments i ON i.id=p.installment_id "
            "JOIN accounts a ON a.id=i.account_id "
            "WHERE p.bank_id IS NOT NULL AND p.reversed=0 "
            "AND p.id NOT IN (SELECT payment_id FROM finance_matches WHERE payment_id IS NOT NULL)"
        )
    ]


def _close(payment, posted_on):
    moment = str(payment.get("paid_on") or payment.get("date") or payment.get("created_at") or "")[
        :10
    ]
    try:
        return abs((date.fromisoformat(moment) - date.fromisoformat(posted_on)).days) <= NEAR_DAYS
    except ValueError:
        return False
