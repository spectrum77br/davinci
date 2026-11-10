"""Accounts from a spreadsheet, as Bling's "Importação de registros financeiros".

The CSV is read, each line becomes a manual account and goes through the same rules as one typed
on the screen. First a preview: every line is tried inside a transaction that is rolled back, so
the person sees exactly what would be refused and why, and nothing is written. Then the import:
each line is its own command keyed by its reference, so sending the same file again does not
duplicate anything.
"""

import csv
import hashlib
import io
import re
from datetime import date

from app.erp_local.core import ERPError

COLUMNS = {
    "tipo": "kind",
    "referencia": "reference",
    "referência": "reference",
    "cliente/fornecedor": "party",
    "fornecedor": "party",
    "cliente": "party",
    "contato": "party",
    "valor": "amount",
    "emissao": "issued_on",
    "emissão": "issued_on",
    "data de emissao": "issued_on",
    "data de emissão": "issued_on",
    "competencia": "competence_on",
    "competência": "competence_on",
    "vencimento": "due_date",
    "historico": "history",
    "histórico": "history",
    "forma de pagamento": "payment_method",
    "categoria": "category",
    "nº documento": "document_number",
    "n° documento": "document_number",
    "numero do documento": "document_number",
    "número do documento": "document_number",
    "documento": "document_number",
    "conta financeira": "bank_id",
}
KINDS = {
    "pagar": "payable",
    "a pagar": "payable",
    "conta a pagar": "payable",
    "p": "payable",
    "despesa": "payable",
    "payable": "payable",
    "receber": "receivable",
    "a receber": "receivable",
    "conta a receber": "receivable",
    "r": "receivable",
    "receita": "receivable",
    "receivable": "receivable",
}
MAX_LINES = 1000


def _day(value):
    value = str(value or "").strip()
    if not value:
        return ""
    match = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", value)
    if match:
        d, m, y = (int(x) for x in match.groups())
        return date(y, m, d).isoformat()
    return date.fromisoformat(value[:10]).isoformat()


def _money(value):
    value = str(value or "").strip().replace("R$", "").replace(" ", "")
    if "," in value:
        value = value.replace(".", "").replace(",", ".")
    return value


def parse(content, default_kind="payable"):
    """Lines of the file as entry payloads, or the reason a line cannot be read."""
    text = content.lstrip("﻿")
    if not text.strip():
        raise ERPError("import_empty", "A planilha está vazia.")
    delimiter = ";" if text.splitlines()[0].count(";") >= text.splitlines()[0].count(",") else ","
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    header = [h.strip().lower() for h in next(reader)]
    fields = [COLUMNS.get(h) for h in header]
    if not {"party", "amount", "due_date"} <= set(fields):
        raise ERPError(
            "import_columns",
            "A planilha precisa das colunas Cliente/Fornecedor, Valor e Vencimento.",
        )
    lines = []
    for number, values in enumerate(reader, start=2):
        if not any(v.strip() for v in values):
            continue
        if len(lines) >= MAX_LINES:
            raise ERPError("import_size", f"Envie até {MAX_LINES} linhas por planilha.")
        raw = {f: (values[i].strip() if i < len(values) else "") for i, f in enumerate(fields) if f}
        try:
            kind = KINDS.get(
                raw.get("kind", "").lower(), default_kind if not raw.get("kind") else None
            )
            if kind is None:
                raise ValueError("Tipo deve ser pagar ou receber.")
            due = _day(raw.get("due_date"))
            payload = {
                "kind": kind,
                "party": raw.get("party", ""),
                "amount": _money(raw.get("amount")),
                "issued_on": _day(raw.get("issued_on")) or due,
                "competence_on": _day(raw.get("competence_on")) or due,
                "due_date": due,
                "history": raw.get("history", ""),
                "payment_method": raw.get("payment_method", ""),
                "category": raw.get("category", ""),
                "document_number": raw.get("document_number", ""),
                "occurrence": "once",
                "count": 1,
            }
            if raw.get("bank_id"):
                payload["bank_id"] = raw["bank_id"]
            # Without a reference column the line itself is the reference: same file, same keys.
            digest = hashlib.sha256(repr(sorted(payload.items())).encode()).hexdigest()[:20]
            payload["reference"] = raw.get("reference") or f"PLANILHA-{digest}"
            lines.append({"line": number, "payload": payload})
        except (ValueError, TypeError) as error:
            lines.append({"line": number, "error": str(error) or "Linha não reconhecida."})
    return lines


def preview(erp, lines):
    """Try every line with the real rules and roll everything back."""
    results = []
    with erp.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            for item in lines:
                if "error" in item:
                    results.append({**item, "ok": False})
                    continue
                key = "finance-import:" + item["payload"]["reference"]
                db.execute("SAVEPOINT linha")
                try:
                    result = erp.execute_in(
                        db, key, "finance.entry.create", item["payload"], "importação"
                    )
                    results.append({**item, "ok": True, "duplicate": bool(result.get("replayed"))})
                    db.execute("RELEASE linha")
                except ERPError as error:
                    db.execute("ROLLBACK TO linha")
                    db.execute("RELEASE linha")
                    results.append({**item, "ok": False, "error": error.message})
        finally:
            db.rollback()
    return results


def commit(erp, lines, actor="importação"):
    done, refused = 0, []
    for item in lines:
        if "error" in item:
            refused.append({"line": item["line"], "error": item["error"]})
            continue
        try:
            erp.command(
                "finance-import:" + item["payload"]["reference"],
                "finance.entry.create",
                item["payload"],
                actor=actor,
            )
            done += 1
        except ERPError as error:
            refused.append({"line": item["line"], "error": error.message})
    return {"imported": done, "refused": refused, "external_writes": False}
