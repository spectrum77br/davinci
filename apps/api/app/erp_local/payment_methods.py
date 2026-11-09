"""Own payment register and frozen sales terms; no payment-provider calls."""

import json
import re
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from uuid import uuid4

from app.erp_local.core import ERPError, dumps, number, text
from app.erp_local.finance import day
from app.services.nfse.texto import documento_valido

SCHEMA = """
CREATE TABLE IF NOT EXISTS own_payment_methods (
 id TEXT PRIMARY KEY, version INTEGER NOT NULL, name TEXT NOT NULL,
 status TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
"""
COMMANDS = {"payment_method.save"}
FIELDS = {
    "name",
    "type",
    "status",
    "applicability",
    "destination",
    "bank_id",
    "days",
    "business_days",
    "holidays",
    "fee_percent",
    "fee_fixed",
    "retention_days",
    "integrated",
    "institution_document",
    "fiscal_code",
    "notes",
}
TYPES = {"cash", "pix", "boleto", "credit_card", "debit_card", "transfer", "other"}


def normalize(db, raw):
    if not isinstance(raw, dict) or set(raw) - FIELDS:
        raise ERPError("payment_method_fields", "Campos da forma de pagamento inválidos.")
    data = {
        k: raw.get(k, default)
        for k, default in {
            "type": "other",
            "status": "active",
            "applicability": "both",
            "destination": "financial",
            "business_days": False,
            "integrated": False,
        }.items()
    }
    data["name"] = text(raw.get("name"), "descrição")
    if (
        any(
            not isinstance(data[k], str) for k in ("type", "status", "applicability", "destination")
        )
        or data["type"] not in TYPES
        or data["status"] not in {"active", "inactive"}
        or data["applicability"] not in {"payable", "receivable", "both"}
        or data["destination"] not in {"financial", "cash", "none"}
        or not isinstance(data["business_days"], bool)
        or not isinstance(data["integrated"], bool)
    ):
        raise ERPError("payment_method_type", "Confira tipo, situação, destino e aplicabilidade.")
    days = raw.get("days", [0])
    if (
        not isinstance(days, list)
        or not 1 <= len(days) <= 120
        or any(isinstance(n, bool) or not isinstance(n, int) or not 0 <= n <= 3650 for n in days)
        or days != sorted(set(days))
    ):
        raise ERPError(
            "payment_method_days", "Informe prazos únicos e crescentes de 0 a 3650 dias."
        )
    data["days"] = days
    holidays = raw.get("holidays", [])
    if not isinstance(holidays, list) or len(holidays) > 365:
        raise ERPError("payment_method_holidays", "Calendário de feriados inválido.")
    data["holidays"] = sorted({day(v) for v in holidays})
    data["fee_percent"] = str(number(raw.get("fee_percent", "0"), unit=True, zero=True))
    data["fee_fixed"] = str(number(raw.get("fee_fixed", "0"), money=True, zero=True))
    if Decimal(data["fee_percent"]) > 100:
        raise ERPError("payment_method_fee", "A taxa percentual não pode ultrapassar 100%.")
    delay = raw.get("retention_days", 0)
    if isinstance(delay, bool) or not isinstance(delay, int) or not 0 <= delay <= 3650:
        raise ERPError("payment_method_retention", "Prazo de recebimento inválido.")
    data["retention_days"] = delay
    data["bank_id"] = text(raw["bank_id"], "conta financeira") if raw.get("bank_id") else ""
    if (
        data["bank_id"]
        and not db.execute("SELECT 1 FROM finance_banks WHERE id=?", (data["bank_id"],)).fetchone()
    ):
        raise ERPError("bank_not_found", "Conta financeira não cadastrada.", 404)
    document = re.sub(r"[.\-/\s]", "", str(raw.get("institution_document") or "")).upper()
    if document and (
        not re.fullmatch(r"[A-Z0-9]{12}\d{2}", document) or not documento_valido(document)
    ):
        raise ERPError("payment_method_document", "CNPJ da instituição inválido.")
    if data["integrated"] and data["type"] in {"pix", "credit_card", "debit_card"} and not document:
        raise ERPError("payment_method_document", "Informe o CNPJ da instituição integrada.")
    data["institution_document"] = document
    code = str(raw.get("fiscal_code") or "")
    if code and not re.fullmatch(r"\d{1,2}", code):
        raise ERPError("payment_method_fiscal", "Código fiscal deve ter até dois dígitos.")
    data["fiscal_code"] = code
    data["notes"] = text(raw["notes"], "observações", 2000) if raw.get("notes") else ""
    return data


def apply(erp, db, p):
    if not isinstance(p, dict) or set(p) - {"id", "version", "data"}:
        raise ERPError("payment_method_fields", "Campos da forma de pagamento inválidos.")
    previous = db.execute("SELECT * FROM own_payment_methods WHERE id=?", (p.get("id"),)).fetchone()
    if p.get("id") and not previous:
        raise ERPError("payment_method_not_found", "Forma de pagamento não encontrada.", 404)
    version = previous["version"] if previous else 0
    if type(p.get("version", 0)) is not int or p.get("version", 0) != version:
        raise ERPError("payment_method_version", "O cadastro mudou. Atualize antes de salvar.", 409)
    data = normalize(db, p.get("data"))
    if db.execute(
        "SELECT 1 FROM own_payment_methods WHERE lower(name)=lower(?) AND id!=?",
        (data["name"], p.get("id") or ""),
    ).fetchone():
        raise ERPError("payment_method_duplicate", "Já existe uma forma com esta descrição.", 409)
    identifier, now = previous["id"] if previous else str(uuid4()), erp.now()
    db.execute(
        "INSERT INTO own_payment_methods VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
        "version=excluded.version,name=excluded.name,status=excluded.status,data=excluded.data,"
        "updated_at=excluded.updated_at",
        (
            identifier,
            version + 1,
            data["name"],
            data["status"],
            dumps(data),
            previous["created_at"] if previous else now,
            now,
        ),
    )
    return {"id": identifier, "version": version + 1, "external_writes": False}


def snapshot(db):
    return [
        {**dict(r), "data": json.loads(r["data"])}
        for r in db.execute("SELECT * FROM own_payment_methods ORDER BY name COLLATE NOCASE,id")
    ]


def freeze(db, identifier, applicability="receivable"):
    row = db.execute("SELECT * FROM own_payment_methods WHERE id=?", (identifier,)).fetchone()
    if not row:
        raise ERPError("payment_method_not_found", "Forma de pagamento não encontrada.", 404)
    data = json.loads(row["data"])
    if row["status"] != "active" or data["applicability"] not in {"both", applicability}:
        raise ERPError(
            "payment_method_unavailable", "Forma de pagamento indisponível para esta operação.", 409
        )
    return {"id": row["id"], "version": row["version"], **data}


def add_days(start, offset, business_days, holidays):
    current = date.fromisoformat(start)
    try:
        if not business_days:
            return (current + timedelta(days=offset)).isoformat()
        while offset:
            current += timedelta(days=1)
            if current.weekday() < 5 and current.isoformat() not in holidays:
                offset -= 1
        # A zero-day term on a weekend/holiday is moved to the next working day.
        while current.weekday() >= 5 or current.isoformat() in holidays:
            current += timedelta(days=1)
    except (ValueError, OverflowError):
        raise ERPError(
            "payment_method_calendar", "Prazo ultrapassa o calendário suportado."
        ) from None
    return current.isoformat()


def plan(frozen, amount, start):
    """Split principal exactly; fees are forecasts, never automatic cash entries."""
    amount = number(amount, money=True, zero=True)
    if not amount:
        return [], []
    cents, count = int(amount * 100), len(frozen["days"])
    base, extra = divmod(cents, count)
    if not base:
        raise ERPError("payment_method_amount", "Cada parcela deve ter ao menos um centavo.")
    result, forecasts = [], []
    for index, offset in enumerate(frozen["days"]):
        principal = Decimal(base + (index < extra)) / 100
        due = add_days(start, offset, frozen["business_days"], frozen["holidays"])
        fee = (
            principal * Decimal(frozen["fee_percent"]) / 100 + Decimal(frozen["fee_fixed"])
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        result.append({"due_date": due, "amount": f"{principal:.2f}"})
        forecasts.append(
            {
                "due_date": due,
                "principal": f"{principal:.2f}",
                "fee": str(fee),
                "expected_on": add_days(
                    due, frozen["retention_days"], frozen["business_days"], frozen["holidays"]
                ),
            }
        )
    return result, forecasts
