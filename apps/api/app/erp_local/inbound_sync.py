"""Notes issued against our companies, fetched from SEFAZ, and the recipient's manifestation.

Homologation only, like the rest of the fiscal client. SEFAZ blocks whoever asks again too soon, so
an empty or refused answer waits an hour. A manifestation is always a person's decision.
"""

import json
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.erp_local import nfe_xml
from app.erp_local.core import ERPError, dumps, text

SCHEMA = """
CREATE TABLE IF NOT EXISTS inbound_dfe_cursors (
 cnpj TEXT PRIMARY KEY, last_nsu TEXT NOT NULL, max_nsu TEXT NOT NULL, next_at TEXT,
 last_code TEXT, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS inbound_summaries (
 key TEXT PRIMARY KEY, cnpj TEXT NOT NULL, data TEXT NOT NULL, manifest TEXT, manifest_at TEXT,
 protocol TEXT, updated_at TEXT NOT NULL);
"""
COMMANDS = {"inbound.dfe.record", "inbound.manifest.record"}
NS = "{http://www.portalfiscal.inf.br/nfe}"
SAO_PAULO = ZoneInfo("America/Sao_Paulo")
WAIT = timedelta(hours=1)
MAX_PAGES = 10
MANIFESTS = {
    "210200": "Confirmada",
    "210210": "Ciência",
    "210220": "Desconhecida",
    "210240": "Não realizada",
}


def read_summary(xml):
    """The short form SEFAZ gives before the recipient acknowledges the note."""
    # Decompressed from SEFAZ's own answer, with the size limit of the client.
    root = ET.fromstring(xml)  # noqa: S314

    def field(tag):
        found = root.find(NS + tag)
        return (found.text or "").strip() if found is not None else ""

    return {
        "key": field("chNFe"),
        "issuer_document": field("CNPJ") or field("CPF"),
        "issuer_name": field("xNome"),
        "issued_at": field("dhEmi"),
        "operation": field("tpNF"),
        "value": field("vNF"),
        "situation": field("cSitNFe"),
    }


def _cursor(db, cnpj):
    return db.execute("SELECT * FROM inbound_dfe_cursors WHERE cnpj=?", (cnpj,)).fetchone()


def _full_note(payload):
    return re.search(r"<nfeProc\b", payload) is not None


def run_company(erp, company, sefaz, *, now=None):
    """One sync round. Full notes go to the normal import; short ones wait for a manifestation."""
    now = now or datetime.now(UTC)
    cnpj, uf = company["cnpj"], company["address"]["uf"]
    with erp.connection() as db:
        row = _cursor(db, cnpj)
    if row and row["next_at"] and datetime.fromisoformat(row["next_at"]) > now:
        return {"state": "waiting", "next_at": row["next_at"]}
    last = row["last_nsu"] if row else "0"
    notes = summaries = pages = 0
    code = None
    while pages < MAX_PAGES:
        pages += 1
        reply = sefaz.distribution(cnpj, uf, last)
        code = reply["code"]
        found = []
        for document in reply["documents"]:
            if _full_note(document["xml"]):
                erp.command(
                    "inbound-dfe-" + document["nsu"] + "-" + cnpj,
                    "inbound.import",
                    {"xml": document["xml"]},
                )
                notes += 1
            elif "resNFe" in document["schema"]:
                found.append(read_summary(document["xml"]))
        summaries += len(found)
        finished = code != "138" or reply["last_nsu"] == reply["max_nsu"]
        next_at = (now + WAIT).isoformat() if finished else None
        last = reply["last_nsu"] or last
        erp.command(
            f"inbound-dfe-cursor-{cnpj}-{last}-{uuid4()}",
            "inbound.dfe.record",
            {
                "cnpj": cnpj,
                "last_nsu": last,
                "max_nsu": reply["max_nsu"] or last,
                "next_at": next_at,
                "code": code,
                "summaries": found,
            },
        )
        if finished:
            break
    return {"state": "read", "code": code, "notes": notes, "summaries": summaries, "pages": pages}


def manifest(erp, key, kind, reason, company, sefaz, certificate, password):
    if kind not in MANIFESTS:
        raise ERPError("inbound_manifest", "Escolha a manifestação.")
    if kind == "210240":
        reason = text(reason, "justificativa", 255)
        if len(reason) < 15:
            raise ERPError("inbound_manifest", "A justificativa precisa de ao menos 15 letras.")
    moment = datetime.now(UTC).astimezone(SAO_PAULO).isoformat(timespec="seconds")
    unsigned = nfe_xml.event(
        kind,
        uf=company["address"]["uf"],
        cnpj=company["cnpj"],
        key=key,
        moment=moment,
        sequence=1,
        text=reason,
    )
    signed = nfe_xml.sign_element(unsigned, "infEvento", "evento", certificate, password)
    result = sefaz.manifest(signed)
    if not result["accepted"]:
        raise ERPError(
            "inbound_manifest_refused",
            f"A SEFAZ recusou ({result['code']}): {result['message'] or 'sem motivo'}",
            409,
        )
    return erp.command(
        str(uuid4()),
        "inbound.manifest.record",
        {"key": key, "cnpj": company["cnpj"], "kind": kind, "protocol": result["protocol"]},
    )


def apply(erp, db, kind, p):
    now = erp.now()
    if kind == "inbound.manifest.record":
        db.execute(
            "INSERT INTO inbound_summaries VALUES (?,?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET "
            "manifest=excluded.manifest,manifest_at=excluded.manifest_at,"
            "protocol=excluded.protocol,updated_at=excluded.updated_at",
            (p["key"], p["cnpj"], dumps({"key": p["key"]}), p["kind"], now, p["protocol"], now),
        )
        return {"key": p["key"], "manifest": p["kind"]}
    db.execute(
        "INSERT OR REPLACE INTO inbound_dfe_cursors VALUES (?,?,?,?,?,?)",
        (p["cnpj"], p["last_nsu"], p["max_nsu"], p["next_at"], p["code"], now),
    )
    for summary in p["summaries"]:
        db.execute(
            "INSERT INTO inbound_summaries(key,cnpj,data,updated_at) VALUES (?,?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET data=excluded.data,updated_at=excluded.updated_at",
            (summary["key"], p["cnpj"], dumps(summary), now),
        )
    return {"cnpj": p["cnpj"], "summaries": len(p["summaries"])}


def snapshot(db):
    return {
        "summaries": [
            {
                **json.loads(row["data"]),
                "cnpj": row["cnpj"],
                "manifest": row["manifest"],
                "manifest_label": MANIFESTS.get(row["manifest"] or "", ""),
            }
            for row in db.execute(
                "SELECT * FROM inbound_summaries ORDER BY updated_at DESC LIMIT 200"
            )
        ],
        "cursors": [dict(row) for row in db.execute("SELECT * FROM inbound_dfe_cursors")],
    }
