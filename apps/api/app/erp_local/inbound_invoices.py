"""Supplier invoices: the original XML, each line tied to a product, then purchase, stock and bills.

The first booking of a supplier's code is a person's choice; after that the link is reused. A note
without authorization, from homologation or addressed to someone else never moves stock.
"""

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from app.erp_local import contacts, nfe_xml, procurement, purchase_documents
from app.erp_local.core import ERPError, dumps, text

SCHEMA = """
CREATE TABLE IF NOT EXISTS inbound_invoices (
 key TEXT PRIMARY KEY, digest TEXT NOT NULL, xml BLOB NOT NULL, data TEXT NOT NULL,
 status TEXT NOT NULL, purchase_id TEXT, imported_at TEXT NOT NULL, booked_at TEXT);
CREATE TABLE IF NOT EXISTS inbound_links (
 supplier TEXT NOT NULL, code TEXT NOT NULL, sku TEXT NOT NULL, factor TEXT NOT NULL,
 updated_at TEXT NOT NULL, PRIMARY KEY(supplier,code));
"""
COMMANDS = {"inbound.import", "inbound.link", "inbound.book", "inbound.reject"}
NS = "{http://www.portalfiscal.inf.br/nfe}"
UNIT_PRICE = Decimal("0.0001")
QUANTITY = Decimal("0.001")
LIMIT = 8 * 1024 * 1024


def _value(node, tag):
    found = node.find(NS + tag) if node is not None else None
    return (found.text or "").strip() if found is not None else ""


def _deep(node, tag):
    found = node.find(".//" + NS + tag) if node is not None else None
    return (found.text or "").strip() if found is not None else ""


def _money(raw):
    try:
        return str(Decimal(raw or "0"))
    except InvalidOperation:
        raise ERPError("invalid_xml", "Valor inválido no XML.") from None


def parse(xml):
    """Plain reading of the note. Unknown fields stay empty; nothing is guessed."""
    if len(xml.encode("utf-8")) > LIMIT or any(m in xml.upper() for m in ("<!DOCTYPE", "<!ENTITY")):
        raise ERPError("invalid_xml", "XML excede o limite ou contém declaração não permitida.")
    try:
        # Declarations were refused above, which is what makes entity attacks possible.
        root = ET.fromstring(xml)  # noqa: S314
    except ET.ParseError:
        raise ERPError("invalid_xml", "Arquivo não é um XML válido.") from None
    inf = root.find(".//" + NS + "infNFe")
    if inf is None:
        raise ERPError("invalid_xml", "Arquivo não é uma NF-e.")
    key = (inf.get("Id") or "").removeprefix("NFe")
    if not re.fullmatch(r"\d{44}", key) or nfe_xml.check_digit(key[:43]) != key[43]:
        raise ERPError("invalid_xml", "Chave de acesso inválida no XML.")
    ide, issuer, buyer = inf.find(NS + "ide"), inf.find(NS + "emit"), inf.find(NS + "dest")
    place = issuer.find(NS + "enderEmit") if issuer is not None else None
    totals = inf.find(f"{NS}total/{NS}ICMSTot")
    items = []
    for line in inf.findall(NS + "det"):
        product, taxes = line.find(NS + "prod"), line.find(NS + "imposto")
        items.append(
            {
                "line": int(line.get("nItem")),
                "code": _value(product, "cProd"),
                "gtin": re.sub(r"\D", "", _value(product, "cEAN")),
                "name": _value(product, "xProd"),
                "ncm": _value(product, "NCM"),
                "cfop": _value(product, "CFOP"),
                "unit": _value(product, "uCom"),
                "quantity": _money(_value(product, "qCom")),
                "unit_price": _money(_value(product, "vUnCom")),
                "total": _money(_value(product, "vProd")),
                "taxes": {
                    tag: _money(_deep(taxes, tag))
                    for tag in ["vICMS", "vICMSST", "vIPI", "vPIS", "vCOFINS"]
                },
            }
        )
    protocol = root.find(".//" + NS + "infProt")
    status = _value(protocol, "cStat")
    return {
        "key": key,
        "number": _value(ide, "nNF"),
        "series": _value(ide, "serie"),
        "issued_at": _value(ide, "dhEmi") or _value(ide, "dEmi"),
        "nature": _value(ide, "natOp"),
        "environment": _value(ide, "tpAmb"),
        "operation": _value(ide, "tpNF"),
        "protocol": _value(protocol, "nProt"),
        "authorized": status in {"100", "150"},
        "supplier": {
            "document": _value(issuer, "CNPJ") or _value(issuer, "CPF"),
            "name": _value(issuer, "xNome"),
            "trade_name": _value(issuer, "xFant"),
            "state_registration": _value(issuer, "IE"),
            "street": _value(place, "xLgr"),
            "number": _value(place, "nro"),
            "complement": _value(place, "xCpl"),
            "district": _value(place, "xBairro"),
            "city": _value(place, "xMun"),
            "ibge_code": _value(place, "cMun"),
            "state": _value(place, "UF"),
            "postal_code": _value(place, "CEP"),
        },
        "buyer_document": _value(buyer, "CNPJ") or _value(buyer, "CPF"),
        "items": items,
        "totals": {
            "invoice": _money(_value(totals, "vNF")),
            "products": _money(_value(totals, "vProd")),
            "freight": _money(_value(totals, "vFrete")),
            "insurance": _money(_value(totals, "vSeg")),
            "discount": _money(_value(totals, "vDesc")),
            "ipi": _money(_value(totals, "vIPI")),
            "st": _money(_value(totals, "vST")),
            "other": _money(_value(totals, "vOutro")),
        },
        "installments": [
            {
                "number": _value(row, "nDup"),
                "due_date": _value(row, "dVenc"),
                "amount": _value(row, "vDup"),
            }
            for row in inf.findall(f"{NS}cobr/{NS}dup")
        ],
    }


def _companies(db):
    documents = set()
    for row in db.execute("SELECT data FROM fiscal_companies"):
        documents.add(re.sub(r"\D", "", str(json.loads(row[0]).get("cnpj") or "")))
    return documents - {""}


def _row(db, key):
    row = db.execute(
        "SELECT * FROM inbound_invoices WHERE key=?", (text(key, "chave", 44),)
    ).fetchone()
    if not row:
        raise ERPError("inbound_not_found", "Nota de entrada não encontrada.", 404)
    return row


def suggestions(db, note):
    """What is already known about each line: a saved link, or a code that fits one product only."""
    supplier = note["supplier"]["document"]
    links = {
        row["code"]: row
        for row in db.execute("SELECT * FROM inbound_links WHERE supplier=?", (supplier,))
    }
    by_gtin = {}
    for row in db.execute("SELECT sku,data FROM product_details"):
        details = json.loads(row["data"])
        for code in {details.get("gtin"), details.get("gtin_tax")} - {None, ""}:
            by_gtin.setdefault(code, set()).add(row["sku"])
    result = {}
    for item in note["items"]:
        link = links.get(item["code"])
        if link:
            result[item["line"]] = {"sku": link["sku"], "factor": link["factor"], "source": "link"}
            continue
        matches = by_gtin.get(item["gtin"]) or set()
        if len(matches) == 1:
            result[item["line"]] = {"sku": next(iter(matches)), "factor": "1", "source": "gtin"}
    return result


def _supplier_contact(erp, db, supplier):
    document = re.sub(r"\D", "", supplier["document"])
    for row in db.execute("SELECT id,version,data FROM own_contacts"):
        data = json.loads(row["data"])
        if re.sub(r"\D", "", data.get("document") or "") == document:
            if "supplier" in data["roles"]:
                return row["id"]
            return contacts.apply(
                erp,
                db,
                {
                    "id": row["id"],
                    "version": row["version"],
                    "data": {**data, "roles": sorted({*data["roles"], "supplier"})},
                },
            )["id"]
    fields = {k: v for k, v in supplier.items() if v and k != "document"}
    fields["name"] = supplier["name"][:120]
    return contacts.apply(
        erp,
        db,
        {
            "data": {
                **fields,
                "roles": ["supplier"],
                "person_type": "J" if len(document) > 11 else "F",
                "document": document,
            }
        },
    )["id"]


def _purchase_terms(note, lines):
    """Same total as the note. Rounding from the unit conversion goes on record, never lost."""
    totals = {k: Decimal(v) for k, v in note["totals"].items()}
    items = []
    for item, (sku, factor) in zip(note["items"], lines, strict=True):
        quantity = (Decimal(item["quantity"]) * factor).quantize(QUANTITY)
        unit = (Decimal(item["total"]) / quantity).quantize(UNIT_PRICE, rounding=ROUND_HALF_UP)
        items.append({"sku": sku, "quantity": str(quantity), "unit_price": str(unit)})
    subtotal = sum((Decimal(i["quantity"]) * Decimal(i["unit_price"]) for i in items), Decimal(0))
    subtotal = subtotal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    charges = totals["ipi"] + totals["st"] + totals["insurance"] + totals["other"]
    discount = totals["discount"]
    gap = totals["invoice"] - (subtotal + totals["freight"] + charges - discount)
    if gap > 0:
        charges += gap
    else:
        discount -= gap
    return items, totals, charges, discount, gap


def _plan(note, total):
    plan = [
        {"due_date": row["due_date"], "amount": str(Decimal(row["amount"]))}
        for row in note["installments"]
    ]
    if plan and sum(Decimal(i["amount"]) for i in plan) == total:
        return plan
    return [{"due_date": note["issued_at"][:10], "amount": str(total)}]


def _book(erp, db, key, p):
    if set(p) - {"key", "depot", "post_stock", "post_accounts"}:
        raise ERPError("inbound_fields", "Campos do lançamento não reconhecidos.")
    row = _row(db, p.get("key"))
    if row["status"] == "booked":
        return {"key": row["key"], "purchase_id": row["purchase_id"], "duplicate": True}
    if row["status"] == "rejected":
        raise ERPError("inbound_rejected", "Nota recusada não entra no estoque.", 409)
    note = json.loads(row["data"])
    if not note["authorized"]:
        raise ERPError("inbound_unauthorized", "XML sem protocolo de autorização.", 409)
    if note["environment"] != "1":
        raise ERPError("inbound_homologation", "Nota de homologação não entra no estoque.", 409)
    if note["operation"] != "1":
        raise ERPError(
            "inbound_operation", "Nota de entrada do emitente: lance pela devolução.", 409
        )
    ours = _companies(db)
    if ours and re.sub(r"\D", "", note["buyer_document"]) not in ours:
        raise ERPError("inbound_recipient", "A nota não é destinada a uma empresa cadastrada.", 409)
    known = suggestions(db, note)
    missing = [str(i["line"]) for i in note["items"] if i["line"] not in known]
    if missing:
        raise ERPError("inbound_links", f"Vincule os itens: {', '.join(missing)}.", 409)
    lines = [(known[i["line"]]["sku"], Decimal(known[i["line"]]["factor"])) for i in note["items"]]
    items, totals, charges, discount, gap = _purchase_terms(note, lines)
    contact_id = _supplier_contact(erp, db, note["supplier"])
    payload = {
        "contact_id": contact_id,
        "depot": p.get("depot"),
        "items": items,
        "freight": str(totals["freight"]),
        "discount": str(discount),
        "charges": str(charges),
        "installments": _plan(note, totals["invoice"]),
        "number": f"NF{note['number']}-{note['series']}-{note['supplier']['document'][:8]}",
        "date": note["issued_at"][:10],
        "internal_notes": f"NF-e {note['key']}"
        + (f" | arredondamento da conversão: {gap}" if gap else ""),
    }
    saved = purchase_documents.save(erp, db, key, "purchase.create", payload)
    if p.get("post_stock", True):
        procurement.apply(erp, db, key, "purchase.stock", {"id": saved["id"]})
    if p.get("post_accounts", True):
        procurement.apply(erp, db, key, "purchase.accounts", {"id": saved["id"]})
    db.execute(
        "UPDATE inbound_invoices SET status='booked',purchase_id=?,booked_at=? WHERE key=?",
        (saved["id"], erp.now(), row["key"]),
    )
    return {"key": row["key"], "purchase_id": saved["id"], "duplicate": False}


def apply(erp, db, key, kind, p):
    if not isinstance(p, dict):
        raise ERPError("inbound_fields", "Dados da nota inválidos.")
    if kind == "inbound.import":
        if set(p) != {"xml"} or not isinstance(p["xml"], str):
            raise ERPError("invalid_xml", "Envie o XML original da NF-e.")
        note = parse(p["xml"])
        raw = p["xml"].encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        old = db.execute(
            "SELECT digest FROM inbound_invoices WHERE key=?", (note["key"],)
        ).fetchone()
        if old:
            if old["digest"] != digest:
                raise ERPError("xml_identity_conflict", "Já há outro XML com essa chave.", 409)
            return {"key": note["key"], "duplicate": True}
        db.execute(
            "INSERT INTO inbound_invoices VALUES (?,?,?,?,?,?,?,?)",
            (note["key"], digest, raw, dumps(note), "pending", None, erp.now(), None),
        )
        return {"key": note["key"], "duplicate": False}
    if kind == "inbound.link":
        if set(p) != {"key", "line", "sku", "factor"}:
            raise ERPError("inbound_fields", "Informe nota, linha, produto e conversão.")
        note = json.loads(_row(db, p["key"])["data"])
        item = next((i for i in note["items"] if i["line"] == p["line"]), None)
        if not item:
            raise ERPError("inbound_line", "Linha não existe nesta nota.", 404)
        sku = erp.product(db, p["sku"])["sku"]
        if erp.product(db, sku)["kind"] != "simple":
            raise ERPError("inbound_kit", "A entrada vai para as peças, não para o kit.", 409)
        try:
            factor = Decimal(str(p["factor"]))
        except InvalidOperation:
            raise ERPError("inbound_factor", "Conversão inválida.") from None
        if not factor.is_finite() or factor <= 0:
            raise ERPError("inbound_factor", "A conversão precisa ser maior que zero.")
        db.execute(
            "INSERT OR REPLACE INTO inbound_links VALUES (?,?,?,?,?)",
            (note["supplier"]["document"], item["code"], sku, str(factor), erp.now()),
        )
        return {"sku": sku}
    if kind == "inbound.reject":
        row = _row(db, p.get("key"))
        if row["status"] == "booked":
            raise ERPError("inbound_booked", "Estorne a compra antes de recusar a nota.", 409)
        db.execute("UPDATE inbound_invoices SET status='rejected' WHERE key=?", (row["key"],))
        return {"key": row["key"]}
    return _book(erp, db, key, p)


def snapshot(db):
    result = []
    for row in db.execute("SELECT * FROM inbound_invoices ORDER BY imported_at DESC LIMIT 200"):
        note = json.loads(row["data"])
        known = suggestions(db, note)
        result.append(
            {
                **note,
                "status": row["status"],
                "purchase_id": row["purchase_id"],
                "imported_at": row["imported_at"],
                "known": {str(k): v for k, v in known.items()},
            }
        )
    from app.erp_local import inbound_sync

    return {"invoices": result, **inbound_sync.snapshot(db)}
