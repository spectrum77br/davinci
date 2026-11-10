"""Several issuing companies under one ERP: who they are and which one issues for each store.

Companies are read from their own authorized notes and only become issuers when a person adopts
them. A note always carries the real value and the real item of the sale, whichever company issues.
"""

import hashlib
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from app.erp_local.core import ERPError, dumps, text

SCHEMA = """
CREATE TABLE IF NOT EXISTS fiscal_company_candidates (
 cnpj TEXT PRIMARY KEY, data TEXT NOT NULL, read_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS fiscal_technical (
 id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS fiscal_routes (
 channel TEXT NOT NULL, store TEXT NOT NULL, product_type TEXT NOT NULL,
 company_id TEXT NOT NULL REFERENCES fiscal_companies(id), updated_at TEXT NOT NULL,
 PRIMARY KEY(channel,store,product_type));
"""
COMMANDS = {
    "fiscal.candidates.load",
    "fiscal.technical.save",
    "fiscal.company.adopt",
    "fiscal.route.save",
}
PRODUCT_TYPES = ("celular", "mala", "eletro")


def _company_id(db, cnpj):
    for row in db.execute("SELECT id,version,data FROM fiscal_companies"):
        if json.loads(row["data"])["cnpj"] == cnpj:
            return row
    return None


def read_issuer(fragment):
    """The <emit> block of an authorized note, which carries no buyer data."""
    # Comes from notes SEFAZ authorized, read on our own server.
    root = ET.fromstring(fragment)  # noqa: S314
    place = root.find("enderEmit")

    def field(node, tag):
        found = node.find(tag) if node is not None else None
        return (found.text or "").strip() if found is not None else ""

    return {
        "cnpj": field(root, "CNPJ"),
        "name": field(root, "xNome")[:60],
        "fantasy": field(root, "xFant")[:60],
        "ie": field(root, "IE"),
        "crt": field(root, "CRT"),
        "address": {
            "street": field(place, "xLgr")[:60],
            "number": field(place, "nro")[:60],
            "complement": field(place, "xCpl")[:60],
            "district": field(place, "xBairro")[:60],
            "city_code": field(place, "cMun"),
            "city": field(place, "xMun")[:60],
            "uf": field(place, "UF"),
            "zip": field(place, "CEP"),
            "phone": field(place, "fone"),
        },
    }


def candidates_from_read(read):
    """One row per CNPJ, with the numbering of its busiest series."""
    stats = {}
    for row in read["stats"]:
        current = stats.get(row["cnpj"])
        if not current or row["ult30"] > current["ult30"]:
            stats[row["cnpj"]] = row
    result = []
    for row in read["emit"]:
        issuer = read_issuer(row["emit"])
        numbering = stats.get(issuer["cnpj"]) or {}
        result.append(
            {
                **issuer,
                "series": int(numbering.get("serie") or 1),
                "last_number": int(numbering.get("ultimo") or 0),
                "notes_30_days": int(numbering.get("ult30") or 0),
                "last_note_on": numbering.get("ultima") or "",
            }
        )
    return result


def _adopt(erp, db, p):
    from app.erp_local import fiscal_invoices

    if set(p) != {"cnpj"}:
        raise ERPError("fiscal_company", "Informe o CNPJ.")
    row = db.execute(
        "SELECT data FROM fiscal_company_candidates WHERE cnpj=?", (text(p["cnpj"], "CNPJ", 14),)
    ).fetchone()
    if not row:
        raise ERPError("fiscal_candidate", "Empresa não encontrada nas notas lidas.", 404)
    from app.erp_local import would

    technical = db.execute("SELECT data FROM fiscal_technical WHERE id=1").fetchone()
    if not technical and not would.active():
        raise ERPError("fiscal_technical", "Informe antes o responsável técnico da emissão.", 409)
    candidate = json.loads(row["data"])
    existing = _company_id(db, candidate["cnpj"])
    next_number = candidate["last_number"] + 1
    payload = {
        "data": {
            **{
                k: candidate[k]
                for k in ("cnpj", "name", "fantasy", "ie", "crt", "address", "series")
            },
            "next_number": next_number,
            # Homologation only: the owner still has to choose who answers for the software.
            "technical": json.loads(technical["data"]) if technical else {},
        }
    }
    if existing:
        current = json.loads(existing["data"])
        payload["data"].update(
            pis_mode=current["pis_mode"],
            next_number=max(next_number, current["next_number"]),
        )
        payload.update(id=existing["id"], version=existing["version"])
    return fiscal_invoices._save_company(erp, db, payload)


def apply(erp, db, kind, p):
    if not isinstance(p, dict):
        raise ERPError("fiscal_company", "Dados da empresa não reconhecidos.")
    if kind == "fiscal.candidates.load":
        if set(p) != {"rows"} or not isinstance(p["rows"], list):
            raise ERPError("fiscal_company", "Leitura das empresas inválida.")
        for row in p["rows"]:
            db.execute(
                "INSERT OR REPLACE INTO fiscal_company_candidates VALUES (?,?,?)",
                (text(row["cnpj"], "CNPJ", 14), dumps(row), erp.now()),
            )
        return {"loaded": len(p["rows"])}
    if kind == "fiscal.technical.save":
        if set(p) != {"cnpj", "contact", "email", "phone"}:
            raise ERPError("fiscal_technical", "Informe CNPJ, contato, e-mail e telefone.")
        data = {
            "cnpj": re.sub(r"\D", "", str(p["cnpj"])),
            "contact": text(p["contact"], "contato", 60),
            "email": text(p["email"], "e-mail", 60),
            "phone": re.sub(r"\D", "", str(p["phone"])),
        }
        db.execute(
            "INSERT OR REPLACE INTO fiscal_technical VALUES (1,?,?)", (dumps(data), erp.now())
        )
        return {"saved": True}
    if kind == "fiscal.company.adopt":
        return _adopt(erp, db, p)
    if set(p) != {"channel", "store", "product_type", "company_id"}:
        raise ERPError("fiscal_route", "Informe loja, tipo de produto e empresa.")
    product_type = p["product_type"] or ""
    if product_type and product_type not in PRODUCT_TYPES:
        raise ERPError("fiscal_route", "Tipo de produto desconhecido.")
    channel, store = text(p["channel"], "canal"), text(p["store"], "loja")
    if not p["company_id"]:
        db.execute(
            "DELETE FROM fiscal_routes WHERE channel=? AND store=? AND product_type=?",
            (channel, store, product_type),
        )
        return {"removed": True}
    if not db.execute("SELECT 1 FROM fiscal_companies WHERE id=?", (p["company_id"],)).fetchone():
        raise ERPError("fiscal_company", "Empresa emissora não encontrada.", 404)
    db.execute(
        "INSERT OR REPLACE INTO fiscal_routes VALUES (?,?,?,?,?)",
        (channel, store, product_type, p["company_id"], erp.now()),
    )
    return {"saved": True}


def _item_type(db, sku):
    from app.erp_local import categories

    path = categories.document_snapshot(db, sku)["path"]
    name = path[-1]["name"].strip().casefold() if path else ""
    return next((t for t in PRODUCT_TYPES if name.startswith(t)), "")


def route_for_order(db, order):
    """The store's company, or the one for this kind of product if the whole order is that kind."""
    channel, store = order.get("channel"), order.get("store")
    if not (channel and store):
        return None
    routes = {
        row["product_type"]: row["company_id"]
        for row in db.execute(
            "SELECT product_type,company_id FROM fiscal_routes WHERE channel=? AND store=?",
            (channel, store),
        )
    }
    chosen = {routes.get(_item_type(db, item["sku"])) for item in order.get("items") or []}
    if len(chosen) == 1 and None not in chosen:
        return chosen.pop()
    return routes.get("")


def snapshot(db):
    adopted = {
        json.loads(r["data"])["cnpj"]: r["id"] for r in db.execute("SELECT * FROM fiscal_companies")
    }
    technical = db.execute("SELECT data FROM fiscal_technical WHERE id=1").fetchone()
    return {
        "candidates": [
            {**json.loads(row["data"]), "company_id": adopted.get(row["cnpj"])}
            for row in db.execute("SELECT * FROM fiscal_company_candidates ORDER BY cnpj")
        ],
        "technical": json.loads(technical["data"]) if technical else None,
        "routes": [dict(row) for row in db.execute("SELECT * FROM fiscal_routes")],
        "product_types": list(PRODUCT_TYPES),
    }


def load_file(erp, path):
    rows = candidates_from_read(json.loads(Path(path).read_text("utf-8")))
    digest = hashlib.sha256(dumps(rows).encode()).hexdigest()
    return erp.command("fiscal-candidates-" + digest, "fiscal.candidates.load", {"rows": rows})


if __name__ == "__main__":
    from app.erp_local.core import LocalERP

    print(load_file(LocalERP(Path(sys.argv[1])), sys.argv[2]))
