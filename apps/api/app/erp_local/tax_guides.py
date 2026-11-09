"""DIFAL per state from the notes themselves, and the batch file the GNRE portal takes.

Reads the XML of each note, authorized here or imported to the archive, so the figures are the ones
the state saw. Nothing is paid or sent: the file is uploaded by a person. Which fields each state
demands changes by state, so the batch still has to pass the portal's own test before real use.
"""

import json
import re
import xml.etree.ElementTree as ET
from decimal import Decimal
from xml.sax.saxutils import escape

from app.erp_local.core import ERPError

NS = "{http://www.portalfiscal.inf.br/nfe}"
GNRE_NS = "http://www.gnre.pe.gov.br"
RECEITA_DIFAL = "100102"
RECEITA_FCP = "100137"
# São Paulo collects this through its own DARE, not through GNRE.
OWN_GUIDE = {"SP": "DARE-SP"}


def _text(node, tag):
    found = node.find(".//" + NS + tag) if node is not None else None
    return (found.text or "").strip() if found is not None else ""


def read_note(raw):
    """One row per note with DIFAL. A note without it, or unauthorized, gives nothing."""
    # Only XML this ERP generated or validated on import reaches here.
    root = ET.fromstring(raw)  # noqa: S314
    inf = root.find(".//" + NS + "infNFe")
    if inf is None:
        return None
    totals = inf.find(f"{NS}total/{NS}ICMSTot")
    difal = Decimal(_text(totals, "vICMSUFDest") or "0")
    fcp = Decimal(_text(totals, "vFCPUFDest") or "0")
    if not difal and not fcp:
        return None
    dest, emit = inf.find(NS + "dest"), inf.find(NS + "emit")
    return {
        "key": (inf.get("Id") or "").removeprefix("NFe"),
        "number": _text(inf.find(NS + "ide"), "nNF"),
        "issued_on": _text(inf.find(NS + "ide"), "dhEmi")[:10],
        "state": _text(dest, "UF"),
        "difal": str(difal),
        "fcp": str(fcp),
        "issuer": _text(emit, "CNPJ"),
        "recipient_document": _text(dest, "CPF") or _text(dest, "CNPJ"),
        "recipient_name": _text(dest, "xNome"),
        "recipient_city": _text(dest, "cMun"),
    }


def rows(db, date_from, date_to):
    seen = {}
    sources = [
        "SELECT xml FROM fiscal_invoices WHERE status='authorized'",
        "SELECT xml FROM fiscal_xml",
    ]
    for query in sources:
        for row in db.execute(query):
            note = read_note(row[0])
            if note and date_from <= note["issued_on"] <= date_to:
                seen[note["key"]] = note
    return sorted(seen.values(), key=lambda n: (n["state"], n["issued_on"], n["number"]))


def report(db, date_from, date_to):
    states = {}
    notes = rows(db, date_from, date_to)
    for note in notes:
        entry = states.setdefault(
            note["state"],
            {"state": note["state"], "notes": 0, "difal": Decimal(0), "fcp": Decimal(0)},
        )
        entry["notes"] += 1
        entry["difal"] += Decimal(note["difal"])
        entry["fcp"] += Decimal(note["fcp"])
    return {
        "from": date_from,
        "to": date_to,
        "states": [
            {
                **entry,
                "difal": str(entry["difal"]),
                "fcp": str(entry["fcp"]),
                "guide": OWN_GUIDE.get(state, "GNRE"),
            }
            for state, entry in sorted(states.items())
        ],
        "notes": notes,
    }


def _tag(name, value):
    return f"<{name}>{escape(str(value))}</{name}>"


def _item(receita, note, value, due):
    document = re.sub(r"\D", "", note["recipient_document"])
    identifier = _tag("CPF" if len(document) == 11 else "CNPJ", document)
    return (
        "<item>"
        + _tag("receita", receita)
        + f'<documentoOrigem tipo="24">{note["key"]}</documentoOrigem>'
        + _tag("dataVencimento", due)
        + f'<valor tipo="11">{value}</valor>'
        + "<contribuinteDestinatario><identificacao>"
        + identifier
        + "</identificacao>"
        + _tag("razaoSocial", note["recipient_name"][:60])
        + _tag("municipio", note["recipient_city"][2:])
        + "</contribuinteDestinatario></item>"
    )


def gnre_batch(company, notes, *, due, payment):
    """One guide per note and state, as DIFAL per operation is collected."""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", due) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}", payment
    ):
        raise ERPError("guide_date", "Informe vencimento e pagamento.")
    address = company.get("address") or {}
    issuer = (
        "<contribuinteEmitente><identificacao>"
        + _tag("CNPJ", company["cnpj"])
        + "</identificacao>"
        + _tag("razaoSocial", company["name"][:60])
        + _tag("endereco", f"{address.get('street', '')} {address.get('number', '')}".strip()[:60])
        + _tag("municipio", str(address.get("city_code", ""))[2:])
        + _tag("uf", address.get("uf", ""))
        + _tag("cep", address.get("zip", ""))
        + "</contribuinteEmitente>"
    )
    guides = []
    left_out = []
    for note in notes:
        if note["state"] in OWN_GUIDE:
            left_out.append(note["key"])
            continue
        items = ""
        total = Decimal(0)
        for receita, field in ((RECEITA_DIFAL, "difal"), (RECEITA_FCP, "fcp")):
            value = Decimal(note[field])
            if value:
                items += _item(receita, note, f"{value:.2f}", due)
                total += value
        guides.append(
            '<TDadosGNRE versao="2.00">'
            + _tag("ufFavorecida", note["state"])
            + _tag("tipoGnre", "0")
            + issuer
            + f"<itensGNRE>{items}</itensGNRE>"
            + _tag("valorGNRE", f"{total:.2f}")
            + _tag("dataPagamento", payment)
            + "</TDadosGNRE>"
        )
    xml = (
        f'<TLote_GNRE xmlns="{GNRE_NS}" versao="2.00"><guias>{"".join(guides)}</guias></TLote_GNRE>'
    )
    return {"xml": xml, "guides": len(guides), "left_out": left_out}


def batch_from_db(db, company_id, date_from, date_to, due, payment):
    row = db.execute("SELECT data FROM fiscal_companies WHERE id=?", (company_id,)).fetchone()
    if not row:
        raise ERPError("guide_company", "Empresa fiscal não encontrada.", 404)
    company = json.loads(row[0])
    notes = [n for n in rows(db, date_from, date_to) if n["issuer"] == company["cnpj"]]
    if not notes:
        raise ERPError("guide_notes", "Nenhuma nota com DIFAL nesta empresa e período.", 404)
    return gnre_batch(company, notes, due=due, payment=payment)
