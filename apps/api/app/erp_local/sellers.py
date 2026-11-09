"""Own seller register and commission release tied to actual local receipts.

Identity uses the contact register. No credentials, seller access grants or
bank transfers are created. Commission amounts are frozen with the sale.
"""

import json
from decimal import ROUND_HALF_UP, Decimal
from uuid import uuid4

from app.erp_local import contacts
from app.erp_local.core import ERPError, dumps, number, text
from app.erp_local.finance import paid

SCHEMA = """
CREATE TABLE IF NOT EXISTS own_sellers (
 id TEXT PRIMARY KEY, contact_id TEXT UNIQUE REFERENCES own_contacts(id),
 version INTEGER NOT NULL, status TEXT NOT NULL, data TEXT NOT NULL, updated_at TEXT NOT NULL);
"""
COMMANDS = {"seller.save"}
RELEASES = {"proportional", "invoiced", "first", "last"}


def apply(erp, db, p):
    if not isinstance(p, dict) or set(p) - {"id", "version", "contact", "data"}:
        raise ERPError("seller_fields", "Dados do vendedor inválidos.")
    previous = db.execute("SELECT * FROM own_sellers WHERE id=?", (p.get("id"),)).fetchone()
    if p.get("id") and not previous:
        raise ERPError("seller_not_found", "Vendedor não encontrado.", 404)
    version = previous["version"] if previous else 0
    if type(p.get("version", 0)) is not int or p.get("version", 0) != version:
        raise ERPError("seller_version", "O vendedor mudou. Atualize antes de salvar.", 409)
    raw, contact = p.get("data"), p.get("contact")
    if not isinstance(raw, dict) or set(raw) - {"status", "release", "rate", "notes"}:
        raise ERPError("seller_fields", "Regras do vendedor inválidas.")
    if not isinstance(contact, dict) or set(contact) - {"id", "version", "data"}:
        raise ERPError("seller_contact", "Dados cadastrais do vendedor inválidos.")
    if previous and previous["contact_id"] != contact.get("id"):
        raise ERPError("seller_identity", "O contato vinculado não pode ser substituído.", 409)
    status, release = raw.get("status", "active"), raw.get("release", "proportional")
    if (
        not isinstance(status, str)
        or status not in {"active", "inactive"}
        or not isinstance(release, str)
        or release not in RELEASES
    ):
        raise ERPError("seller_rules", "Situação ou regra de comissão inválida.")
    rate = number(raw.get("rate", "0"), unit=True, zero=True)
    if rate > 100:
        raise ERPError("seller_rate", "Alíquota de comissão não pode ultrapassar 100%.")
    contact_id = contacts.apply(erp, db, contact)["id"]
    if db.execute(
        "SELECT 1 FROM own_sellers WHERE contact_id=? AND id!=?", (contact_id, p.get("id") or "")
    ).fetchone():
        raise ERPError("seller_duplicate", "Este contato já está vinculado a um vendedor.", 409)
    identifier = previous["id"] if previous else str(uuid4())
    data = {
        "rate": str(rate),
        "release": release,
        "notes": text(raw["notes"], "observações", 2000) if raw.get("notes") else "",
    }
    db.execute(
        "INSERT INTO own_sellers VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
        "version=excluded.version,status=excluded.status,data=excluded.data,updated_at=excluded.updated_at",
        (identifier, contact_id, version + 1, status, dumps(data), erp.now()),
    )
    return {
        "id": identifier,
        "contact_id": contact_id,
        "version": version + 1,
        "external_writes": False,
    }


def freeze(db, identifier):
    row = db.execute("SELECT * FROM own_sellers WHERE id=?", (identifier,)).fetchone()
    if not row:
        raise ERPError("seller_not_found", "Vendedor não encontrado.", 404)
    if row["status"] != "active":
        raise ERPError("seller_inactive", "Vendedor inativo para novas vendas.", 409)
    return {
        "id": row["id"],
        "version": row["version"],
        **json.loads(row["data"]),
        "contact": contacts.document_snapshot(db, row["contact_id"]),
    }


def bind(db, identifier, old, subtotal, discount):
    if not identifier:
        return None
    previous = old.get("seller")
    result = dict(previous) if previous and previous["id"] == identifier else freeze(db, identifier)
    base = max(Decimal(0), subtotal - discount)
    result["base"] = str(base)
    result["amount"] = str(
        (base * Decimal(result["rate"]) / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    )
    return result


def guard_edit(db, identifier):
    for row in db.execute(
        "SELECT i.id FROM finance_installments i JOIN accounts a ON a.id=i.account_id "
        "WHERE a.document_id=? AND a.kind='commission'",
        (identifier,),
    ):
        if paid(db, row[0]):
            raise ERPError(
                "seller_paid_review", "Estorne as comissões pagas antes de editar a venda.", 409
            )


def ensure_accounts(erp, db):
    """An entitlement exists only when the sale has an actual financial header."""
    for row in db.execute(
        "SELECT d.id,d.data FROM documents d JOIN accounts a ON a.document_id=d.id "
        "WHERE d.kind='order' AND a.kind='receivable'"
    ).fetchall():
        seller = json.loads(row["data"]).get("seller")
        existing = db.execute(
            "SELECT * FROM accounts WHERE document_id=? AND kind='commission'", (row["id"],)
        ).fetchone()
        if not seller or not Decimal(seller["amount"]):
            if existing:
                guard_edit(db, row["id"])
                db.execute("UPDATE accounts SET status='cancelled' WHERE id=?", (existing["id"],))
                db.execute(
                    "UPDATE finance_installments SET active=0 WHERE account_id=?", (existing["id"],)
                )
            continue
        if existing and (
            Decimal(existing["amount"]) != Decimal(seller["amount"])
            or existing["status"] == "cancelled"
        ):
            amounts_paid = sum(
                (
                    paid(db, i[0])
                    for i in db.execute(
                        "SELECT id FROM finance_installments WHERE account_id=?", (existing["id"],)
                    )
                ),
                Decimal(0),
            )
            if amounts_paid:
                raise ERPError(
                    "seller_paid_review",
                    "Estorne as comissões pagas antes de alterar a venda.",
                    409,
                )
            db.execute(
                "UPDATE accounts SET amount=?,status='open' WHERE id=?",
                (seller["amount"], existing["id"]),
            )
            db.execute(
                "UPDATE finance_installments SET active=0 WHERE account_id=?", (existing["id"],)
            )
        elif not existing:
            erp.account(db, row["id"], "commission", seller["amount"])


def entitlement(db, identifier):
    from app.erp_local.returns import active_refund

    row = db.execute(
        "SELECT status,data FROM documents WHERE id=? AND kind='order'", (identifier,)
    ).fetchone()
    if not row:
        raise ERPError("commission_order", "Venda da comissão não encontrada.", 404)
    data = json.loads(row["data"])
    seller = data.get("seller")
    if not seller:
        raise ERPError("commission_seller", "Venda sem vendedor vinculado.", 409)
    header = db.execute(
        "SELECT * FROM accounts WHERE document_id=? AND kind='receivable'", (identifier,)
    ).fetchone()
    total, released, principal = Decimal(seller["amount"]), Decimal(0), Decimal(0)
    installments = []
    if (
        header
        and header["status"] != "cancelled"
        and row["status"] not in {"cancelled", "returned"}
    ):
        installments = db.execute(
            "SELECT * FROM finance_installments WHERE account_id=? AND active=1 ORDER BY rowid",
            (header["id"],),
        ).fetchall()
        done = [min(Decimal(i["amount"]), paid(db, i["id"])) for i in installments]
        principal = sum(done, Decimal(0))
        release = seller["release"]
        if release == "invoiced":
            released = total
        elif release == "proportional" and Decimal(header["amount"]):
            released = min(total, total * principal / Decimal(header["amount"])).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        elif release == "first" and installments and done[0] == Decimal(installments[0]["amount"]):
            released = total
        elif (
            release == "last"
            and installments
            and all(done[n] == Decimal(i["amount"]) for n, i in enumerate(installments))
        ):
            released = total
    account = db.execute(
        "SELECT id FROM accounts WHERE document_id=? AND kind='commission'", (identifier,)
    ).fetchone()
    settled = (
        sum(
            (
                paid(db, i[0])
                for i in db.execute(
                    "SELECT id FROM finance_installments WHERE account_id=?", (account[0],)
                )
            ),
            Decimal(0),
        )
        if account
        else Decimal(0)
    )
    voided = row["status"] in {"cancelled", "returned"} or bool(
        header and header["status"] == "cancelled"
    )
    review = not voided and active_refund(db, identifier)
    return {
        "order_id": identifier,
        "seller_id": seller["id"],
        "seller_name": seller["contact"]["name"],
        "sale_number": data["external_id"],
        "date": data.get("date", ""),
        "sale_total": data["total"],
        "release": seller["release"],
        "rate": seller["rate"],
        "base": seller["base"],
        "total": str(total),
        "released": str(released),
        "pending": str(total - released if not voided else Decimal(0)),
        "paid": str(settled),
        "voided": voided,
        "balance": str(released - settled),
        "recoverable": str(max(Decimal(0), settled - released)),
        "review": review,
        "available_to_pay": str(max(Decimal(0), released - settled) if not review else Decimal(0)),
        "account_id": account[0] if account else None,
    }


def assert_payable(db, identifier, principal):
    state = entitlement(db, identifier)
    if state["review"]:
        raise ERPError(
            "commission_return_review", "Concilie a comissão com o reembolso parcial aprovado.", 409
        )
    if principal > max(Decimal(0), Decimal(state["balance"])):
        raise ERPError(
            "commission_not_released",
            "A comissão ainda não está liberada para esse pagamento.",
            409,
        )


def snapshot(db):
    return {
        "sellers": [
            {
                **dict(r),
                "data": json.loads(r["data"]),
                "contact": next(c for c in contacts.snapshot(db) if c["id"] == r["contact_id"]),
            }
            for r in db.execute("SELECT * FROM own_sellers ORDER BY rowid DESC")
        ],
        "commissions": [
            entitlement(db, r[0])
            for r in db.execute(
                "SELECT id FROM documents WHERE kind='order' "
                "AND json_type(data,'$.seller')='object' ORDER BY rowid DESC"
            )
        ],
        "external_payments": False,
    }
