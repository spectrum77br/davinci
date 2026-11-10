"""Own customer/supplier register. No address lookup, SEFAZ acknowledgement or messages."""

import json
import re
from datetime import date
from uuid import uuid4

from app.erp_local.core import ERPError, dumps, text
from app.services.nfse.texto import documento_valido

SCHEMA = """
CREATE TABLE IF NOT EXISTS own_contacts (
 id TEXT PRIMARY KEY, code INTEGER UNIQUE NOT NULL, version INTEGER NOT NULL,
 document TEXT UNIQUE, status TEXT NOT NULL, data TEXT NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
"""
COMMANDS = {"contact.save", "contact.delete", "contact.restore", "contact.link_seller"}
STATES = set(
    "AC AL AM AP BA CE DF ES GO MA MG MS MT PA PB PE PI PR RJ RN RO RR RS SC SE SP TO EX".split()
)
ROLES = {"customer", "supplier", "carrier", "technician", "accountant", "seller"}
STRINGS = {
    "name": 120,
    "trade_name": 120,
    "state_registration": 30,
    "rg": 30,
    "issuer": 30,
    "city": 120,
    "district": 120,
    "street": 160,
    "number": 30,
    "complement": 120,
    "country": 60,
    "contact_info": 300,
    "phone": 30,
    "fax": 30,
    "mobile": 30,
    "email": 160,
    "invoice_email": 500,
    "website": 200,
    "notes": 2000,
}
FIELDS = {
    *STRINGS,
    "person_type",
    "document",
    "contributor",
    "postal_code",
    "state",
    "ibge_code",
    "customer_since",
    "status",
    "roles",
    "seller_id",
    "birth_date",
}


def normalize(raw):
    if not isinstance(raw, dict) or set(raw) - FIELDS:
        raise ERPError("contact_fields", "Campos do contato não reconhecidos.")
    data = {
        key: text(raw[key], key, limit) if raw.get(key) else "" for key, limit in STRINGS.items()
    }
    data["name"] = text(raw.get("name"), "nome")
    data["person_type"] = raw.get("person_type", "F")
    data["status"] = raw.get("status", "active")
    data["contributor"] = str(raw.get("contributor", "9"))
    if (
        data["person_type"] not in {"F", "J", "E"}
        or data["status"] not in {"active", "inactive", "idle"}
        or data["contributor"] not in {"1", "2", "9"}
    ):
        raise ERPError("contact_type", "Tipo de pessoa, contribuinte ou situação inválidos.")
    roles = raw.get("roles", [])
    if not isinstance(roles, list) or any(not isinstance(r, str) or r not in ROLES for r in roles):
        raise ERPError("contact_roles", "Tipos de contato não reconhecidos.")
    data["roles"] = sorted(set(roles))
    document = text(raw["document"], "documento", 40) if raw.get("document") else ""
    if data["person_type"] != "E":
        document = re.sub(r"[.\-/\s]", "", document).upper()
        pattern = r"\d{11}" if data["person_type"] == "F" else r"[A-Z0-9]{12}\d{2}"
        if document and (not re.fullmatch(pattern, document) or not documento_valido(document)):
            raise ERPError(
                "contact_document", "CPF/CNPJ inválido. Confira os dígitos e o tipo de pessoa."
            )
    elif not data["country"] or data["country"].casefold() in {"brasil", "brazil", "br"}:
        raise ERPError("contact_country", "Informe o país do contato estrangeiro.")
    data["document"] = document
    for key, size in [("postal_code", 8), ("ibge_code", 7)]:
        value = re.sub(r"[-.\s]", "", str(raw.get(key) or ""))
        if value and (not value.isascii() or not value.isdigit() or len(value) != size):
            raise ERPError("contact_address", "Confira o CEP e o código IBGE do município.")
        data[key] = value
    data["state"] = str(raw.get("state") or "").upper()
    if data["state"] and data["state"] not in STATES:
        raise ERPError("contact_state", "UF não reconhecida.")
    for key in ["email", "invoice_email"]:
        emails = re.split(r"[;,]", data[key]) if data[key] else []
        if len(emails) > 5 or any(
            not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", e.strip()) for e in emails
        ):
            raise ERPError("contact_email", "Confira o e-mail do contato.")
        data[key] = "; ".join(e.strip() for e in emails)
    try:
        data["customer_since"] = (
            date.fromisoformat(raw["customer_since"]).isoformat()
            if raw.get("customer_since")
            else ""
        )
    except (ValueError, TypeError):
        raise ERPError("contact_date", "Data do cadastro inválida.") from None
    try:
        data["birth_date"] = (
            date.fromisoformat(raw["birth_date"]).isoformat() if raw.get("birth_date") else ""
        )
    except (ValueError, TypeError):
        raise ERPError("contact_date", "Data de nascimento inválida.") from None
    data["seller_id"] = text(raw["seller_id"], "vendedor") if raw.get("seller_id") else ""
    return data


def _seller_exists(db, identifier):
    try:
        return bool(db.execute("SELECT 1 FROM own_sellers WHERE id=?", (identifier,)).fetchone())
    except Exception:  # noqa: BLE001 - the seller register appears with its own schema
        return False


def _store(erp, db, row, data, status):
    version = row["version"] + 1
    db.execute(
        "UPDATE own_contacts SET version=?,status=?,data=?,updated_at=? WHERE id=?",
        (version, status, dumps(data), erp.now(), row["id"]),
    )
    return {"id": row["id"], "code": row["code"], "version": version, "external_writes": False}


def _change(erp, db, p, kind):
    """Bling's trash (excluir/restaurar) and the link to a seller, many contacts at once."""
    if kind == "contact.link_seller":
        if not isinstance(p, dict) or set(p) != {"ids", "seller_id"}:
            raise ERPError("contact_fields", "Informe os contatos e o vendedor.")
        ids = p["ids"]
        if not isinstance(ids, list) or not 1 <= len(ids) <= 500 or len(set(ids)) != len(ids):
            raise ERPError("contact_fields", "Selecione de 1 a 500 contatos.")
        seller = text(p["seller_id"], "vendedor") if p["seller_id"] else ""
        if seller and not _seller_exists(db, seller):
            raise ERPError("contact_seller", "Vendedor não encontrado.", 404)
        results = []
        for identifier in ids:
            row = db.execute("SELECT * FROM own_contacts WHERE id=?", (identifier,)).fetchone()
            if not row:
                raise ERPError("contact_not_found", "Contato não encontrado.", 404)
            if row["status"] == "deleted":
                raise ERPError("contact_deleted", "Restaure o contato antes de alterar.", 409)
            data = {**json.loads(row["data"]), "seller_id": seller}
            results.append(_store(erp, db, row, data, row["status"]))
        return {"contacts": results, "external_writes": False}
    if not isinstance(p, dict) or set(p) != {"id", "version"}:
        raise ERPError("contact_fields", "Informe o contato e a versão.")
    row = db.execute(
        "SELECT * FROM own_contacts WHERE id=?", (text(p["id"], "contato"),)
    ).fetchone()
    if not row:
        raise ERPError("contact_not_found", "Contato não encontrado.", 404)
    if isinstance(p["version"], bool) or p["version"] != row["version"]:
        raise ERPError("contact_conflict", "O contato mudou. Atualize a tela antes.", 409)
    data = json.loads(row["data"])
    if kind == "contact.delete":
        if row["status"] == "deleted":
            raise ERPError("contact_deleted", "O contato já está na lixeira.", 409)
        # Documents keep their own copy of the contact; the register only leaves the lists.
        data = {**data, "status_before": row["status"], "status": "deleted"}
        return _store(erp, db, row, data, "deleted")
    if row["status"] != "deleted":
        raise ERPError("contact_not_deleted", "O contato não está na lixeira.", 409)
    status = data.pop("status_before", "active")
    return _store(erp, db, row, {**data, "status": status}, status)


def apply(erp, db, p, kind="contact.save"):
    if kind != "contact.save":
        return _change(erp, db, p, kind)
    if not isinstance(p, dict) or set(p) - {"id", "version", "data"}:
        raise ERPError("contact_fields", "Campos do cadastro não reconhecidos.")
    data = normalize(p.get("data"))
    if data["seller_id"] and not _seller_exists(db, data["seller_id"]):
        raise ERPError("contact_seller", "Vendedor não encontrado.", 404)
    previous = None
    if p.get("id"):
        previous = db.execute(
            "SELECT * FROM own_contacts WHERE id=?", (text(p["id"], "contato"),)
        ).fetchone()
        if not previous:
            raise ERPError("contact_not_found", "Contato não encontrado.", 404)
        if previous["status"] == "deleted":
            raise ERPError("contact_deleted", "Restaure o contato antes de editar.", 409)
        if isinstance(p.get("version"), bool) or p.get("version") != previous["version"]:
            raise ERPError(
                "contact_conflict", "O contato mudou. Atualize a tela antes de salvar.", 409
            )
    elif p.get("version") is not None:
        raise ERPError("contact_version", "Contato novo não possui versão anterior.")
    document_key = (
        dumps(
            [
                data["person_type"],
                data["country"].casefold() if data["person_type"] == "E" else "BR",
                data["document"],
            ]
        )
        if data["document"]
        else None
    )
    duplicate = db.execute(
        "SELECT id FROM own_contacts WHERE document=?", (document_key,)
    ).fetchone()
    if duplicate and (not previous or duplicate["id"] != previous["id"]):
        raise ERPError("contact_duplicate", "Documento já cadastrado em outro contato.", 409)
    identifier = previous["id"] if previous else str(uuid4())
    code = (
        previous["code"]
        if previous
        else db.execute("SELECT coalesce(max(code),0)+1 FROM own_contacts").fetchone()[0]
    )
    version = previous["version"] + 1 if previous else 1
    db.execute(
        "INSERT INTO own_contacts VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
        "version=excluded.version,document=excluded.document,status=excluded.status,"
        "data=excluded.data,updated_at=excluded.updated_at",
        (
            identifier,
            code,
            version,
            document_key,
            data["status"],
            dumps(data),
            erp.now(),
            erp.now(),
        ),
    )
    return {"id": identifier, "code": code, "version": version, "external_writes": False}


def document_snapshot(db, identifier):
    row = db.execute(
        "SELECT * FROM own_contacts WHERE id=?", (text(identifier, "contato"),)
    ).fetchone()
    if not row:
        raise ERPError("contact_not_found", "Contato não encontrado.", 404)
    if row["status"] != "active":
        raise ERPError("contact_inactive", "Ative o contato antes de criar uma nova operação.", 409)
    data = json.loads(row["data"])
    return {
        "id": row["id"],
        "code": row["code"],
        "version": row["version"],
        **{
            k: data[k]
            for k in [
                "name",
                "trade_name",
                "person_type",
                "document",
                "contributor",
                "state_registration",
                "postal_code",
                "state",
                "city",
                "ibge_code",
                "district",
                "street",
                "number",
                "complement",
                "country",
                "phone",
                "mobile",
                "email",
                "invoice_email",
            ]
        },
    }


def snapshot(db):
    return [
        {
            "id": r["id"],
            "code": r["code"],
            "version": r["version"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
            "data": json.loads(r["data"]),
        }
        for r in db.execute("SELECT * FROM own_contacts ORDER BY code")
    ]
