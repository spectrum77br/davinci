"""Own NF-e flow: issuers, numbering, preparation from the frozen order, and SEFAZ outcomes.

Preparing touches neither stock nor accounts. Each send records its intent first, so a lost reply
leaves the note marked for consultation instead of a second send under the same number.
"""

import json
import re
import secrets
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

from stdnum.br import cnpj as cnpj_rules
from stdnum.br import cpf as cpf_rules

from app.erp_local import nfe_rules, nfe_xml
from app.erp_local.core import ERPError, dumps, text

SCHEMA = """
CREATE TABLE IF NOT EXISTS fiscal_companies (
 id TEXT PRIMARY KEY, version INTEGER NOT NULL, data TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS fiscal_invoices (
 id TEXT PRIMARY KEY, order_id TEXT NOT NULL,
 company_id TEXT NOT NULL REFERENCES fiscal_companies(id),
 series INTEGER NOT NULL, number INTEGER NOT NULL, access_key TEXT NOT NULL,
 status TEXT NOT NULL, version INTEGER NOT NULL, data TEXT NOT NULL, xml TEXT NOT NULL,
 signed_xml TEXT, authorized_xml TEXT, updated_at TEXT NOT NULL,
 UNIQUE(company_id,series,number));
CREATE TABLE IF NOT EXISTS fiscal_invoice_events (
 invoice_id TEXT NOT NULL REFERENCES fiscal_invoices(id), kind TEXT NOT NULL,
 sequence INTEGER NOT NULL, status TEXT NOT NULL, text TEXT NOT NULL, protocol TEXT,
 code TEXT, message TEXT, xml TEXT, created_at TEXT NOT NULL,
 PRIMARY KEY(invoice_id,kind,sequence));
CREATE TABLE IF NOT EXISTS fiscal_number_gaps (
 id TEXT PRIMARY KEY, company_id TEXT NOT NULL REFERENCES fiscal_companies(id),
 series INTEGER NOT NULL, first_number INTEGER NOT NULL, last_number INTEGER NOT NULL,
 reason TEXT NOT NULL, status TEXT NOT NULL, protocol TEXT, code TEXT, message TEXT,
 created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS fiscal_invoice_history (
 invoice_id TEXT NOT NULL REFERENCES fiscal_invoices(id), version INTEGER NOT NULL,
 status TEXT NOT NULL, detail TEXT NOT NULL, operation_key TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(invoice_id,version));
"""
COMMANDS = {
    "fiscal.company.save",
    "fiscal.invoice.prepare",
    "fiscal.invoice.prepare_return",
    "fiscal.invoice.mark_sending",
    "fiscal.invoice.record",
    "fiscal.invoice.event_record",
    "fiscal.gap.record",
}
ACTIVE = {"draft", "sending", "authorized"}
SOFTWARE = "DaVinci ERP 1.0"
SAO_PAULO = ZoneInfo("America/Sao_Paulo")


def _digits(value):
    return re.sub(r"\D", "", str(value or ""))


def _company_data(raw):
    if not isinstance(raw, dict):
        raise ERPError("fiscal_company", "Dados da empresa não reconhecidos.")
    document = _digits(raw.get("cnpj"))
    if not cnpj_rules.is_valid(document):
        raise ERPError("fiscal_company_cnpj", "CNPJ da empresa inválido.")
    address = raw.get("address") or {}
    uf = address.get("uf")
    if uf not in nfe_xml.UF_CODES:
        raise ERPError("fiscal_company_address", "Informe a UF da empresa.")
    data = {
        "cnpj": document,
        "name": text(raw.get("name"), "razão social", 60),
        "fantasy": text(raw["fantasy"], "nome fantasia", 60) if raw.get("fantasy") else "",
        "ie": _digits(raw.get("ie")) or "",
        "crt": str(raw.get("crt", "")),
        "pis_mode": raw.get("pis_mode", "outr"),
        "series": raw.get("series", 1),
        "next_number": raw.get("next_number", 1),
        "address": {
            "street": text(address.get("street"), "logradouro", 60),
            "number": text(address.get("number"), "número", 60),
            "complement": text(address["complement"], "complemento", 60)
            if address.get("complement")
            else "",
            "district": text(address.get("district"), "bairro", 60),
            "city_code": _digits(address.get("city_code")),
            "city": text(address.get("city"), "município", 60),
            "uf": uf,
            "zip": _digits(address.get("zip")),
            "phone": _digits(address.get("phone")),
        },
        "technical": raw.get("technical") or {},
    }
    if data["crt"] not in nfe_rules.PROFILES:
        raise ERPError("fiscal_company_crt", "Regime tributário sem perfil homologado.")
    if data["pis_mode"] not in {"outr", "nt"}:
        raise ERPError("fiscal_company_pis", "Escolha o tratamento de PIS/COFINS.")
    if not data["ie"]:
        raise ERPError("fiscal_company_ie", "Informe a inscrição estadual.")
    if len(data["address"]["city_code"]) != 7 or len(data["address"]["zip"]) != 8:
        raise ERPError("fiscal_company_address", "Confira código do município e CEP.")
    if type(data["series"]) is not int or not 0 <= data["series"] <= 889:
        raise ERPError("fiscal_company_series", "Série fora do intervalo aceito.")
    if type(data["next_number"]) is not int or not 1 <= data["next_number"] <= 999999999:
        raise ERPError("fiscal_company_number", "Próximo número fora do intervalo aceito.")
    technical = data["technical"]
    from app.erp_local import would

    if not technical and would.active():
        return data
    if set(technical) != {"cnpj", "contact", "email", "phone"} or not cnpj_rules.is_valid(
        _digits(technical.get("cnpj"))
    ):
        raise ERPError("fiscal_company_technical", "Informe o responsável técnico da emissão.")
    return data


def _save_company(erp, db, p):
    if set(p) - {"id", "version", "data"}:
        raise ERPError("fiscal_company", "Dados da empresa não reconhecidos.")
    data = _company_data(p.get("data"))
    identifier = p.get("id") or str(uuid4())
    row = db.execute("SELECT * FROM fiscal_companies WHERE id=?", (identifier,)).fetchone()
    version = row["version"] if row else 0
    if type(p.get("version", 0)) is not int or p.get("version", 0) != version:
        raise ERPError("fiscal_company_version", "A empresa mudou. Atualize antes de salvar.", 409)
    if row and data["next_number"] < json.loads(row["data"])["next_number"]:
        raise ERPError("fiscal_company_number", "O próximo número não pode voltar.", 409)
    for other in db.execute("SELECT id,data FROM fiscal_companies"):
        if other["id"] != identifier and json.loads(other["data"])["cnpj"] == data["cnpj"]:
            raise ERPError("fiscal_company_cnpj", "Já existe empresa com este CNPJ.", 409)
    db.execute(
        "INSERT INTO fiscal_companies VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
        "version=excluded.version,data=excluded.data,updated_at=excluded.updated_at",
        (identifier, version + 1, dumps(data), erp.now()),
    )
    return {"id": identifier, "version": version + 1, "external_writes": False}


def _share(total, parts):
    """Split a value by weight; the last line takes the rounding so the note always adds up."""
    amount = Decimal(str(total or 0))
    whole = sum(parts) or Decimal(1)
    result, used = [], Decimal(0)
    for index, part in enumerate(parts):
        if index == len(parts) - 1:
            result.append(amount - used)
        else:
            value = (amount * part / whole).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            result.append(value)
            used += value
    return result


def _recipient(order, reasons):
    contact = order.get("contact")
    if not contact:
        reasons.append("Pedido sem cliente cadastrado para a nota.")
        return None
    document = _digits(contact.get("document"))
    if not (cpf_rules.is_valid(document) or cnpj_rules.is_valid(document)):
        reasons.append("CPF ou CNPJ do destinatário inválido.")
    if contact.get("state") not in nfe_xml.UF_CODES:
        reasons.append("UF do destinatário ausente.")
    code, zip_code = _digits(contact.get("ibge_code")), _digits(contact.get("postal_code"))
    if not code:
        from app.erp_local.cutover import city_code

        code = city_code(contact.get("city"), contact.get("state"))
    if len(code) != 7:
        reasons.append("Código IBGE do município do destinatário ausente.")
    if len(zip_code) != 8:
        reasons.append("CEP do destinatário ausente.")
    for field, label in [("street", "logradouro"), ("district", "bairro"), ("city", "município")]:
        if not contact.get(field):
            reasons.append(f"Destinatário sem {label}.")
    contributor = str(contact.get("contributor") or "9")
    return {
        "document": document,
        "name": contact.get("name") or order.get("customer_name", ""),
        "ie_indicator": contributor if contributor in {"1", "2", "9"} else "9",
        "ie": _digits(contact.get("state_registration")) if contributor == "1" else "",
        "email": (contact.get("email") or "").split(",")[0].strip(),
        "address": {
            "street": contact.get("street") or "",
            "number": contact.get("number") or "S/N",
            "complement": contact.get("complement") or "",
            "district": contact.get("district") or "",
            "city_code": code,
            "city": contact.get("city") or "",
            "uf": contact.get("state") or "",
            "zip": zip_code,
            "phone": _digits(contact.get("phone") or contact.get("mobile")),
        },
    }


def _lines(company, order, recipient, reasons):
    snapshots = order.get("product_snapshots", {})
    rows = order.get("items", [])
    gross = [
        (Decimal(i["quantity"]) * Decimal(i["unit_price"])).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        for i in rows
    ]
    discounts = _share(order.get("discount"), gross)
    freights = _share(order.get("freight"), gross)
    origin_uf = company["address"]["uf"]
    destination_uf = recipient["address"]["uf"] if recipient else origin_uf
    contributor = bool(recipient) and recipient["ie_indicator"] == "1"
    items, net, weight, tracked = [], Decimal(0), Decimal(0), True
    for index, item in enumerate(rows):
        snapshot = snapshots.get(item["sku"], {})
        tax = (snapshot.get("tax") or {}).get("data") or {}
        label = item["sku"]
        if snapshot.get("type") == "service":
            reasons.append(f"{label}: serviço não entra em NF-e de mercadoria.")
            continue
        if not tax.get("ncm") or len(tax["ncm"]) != 8:
            reasons.append(f"{label}: NCM ausente no cadastro usado pelo pedido.")
        if tax.get("origin") in (None, ""):
            reasons.append(f"{label}: origem da mercadoria ausente.")
        if not snapshot.get("unit"):
            reasons.append(f"{label}: unidade ausente.")
        base = gross[index] - discounts[index] + freights[index]
        try:
            cfop, lines = nfe_rules.item_tax(
                company,
                origin_uf,
                destination_uf,
                base=base,
                origin=tax.get("origin") if tax.get("origin") not in (None, "") else "0",
                contributor=contributor,
            )
        except ValueError as error:
            reasons.append(f"{label}: {error}")
            continue
        percent = tax.get("approximate_tax_percent")
        if percent:
            lines["approximate"] = str(
                (gross[index] * Decimal(percent) / 100).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
            )
        if snapshot.get("net_weight_kg") is None or snapshot.get("gross_weight_kg") is None:
            tracked = False
        if tracked:
            net += Decimal(snapshot["net_weight_kg"]) * Decimal(item["quantity"])
            weight += Decimal(snapshot["gross_weight_kg"]) * Decimal(item["quantity"])
        items.append(
            {
                "sku": item["sku"],
                "name": snapshot.get("name") or item["sku"],
                "gtin": snapshot.get("gtin") if snapshot.get("gtin") != "SEM GTIN" else "",
                "gtin_tax": snapshot.get("gtin_tax")
                if snapshot.get("gtin_tax") != "SEM GTIN"
                else "",
                "ncm": tax.get("ncm", ""),
                "cest": tax.get("cest", ""),
                "unit": snapshot.get("unit") or "UN",
                "cfop": cfop,
                "quantity": item["quantity"],
                "unit_price": item["unit_price"],
                "total": str(gross[index]),
                "discount": str(discounts[index]),
                "freight": str(freights[index]),
                "order_line": index + 1,
                "tax": lines,
            }
        )
    return items, (net, weight) if tracked and items else (None, None)


def build_spec(company, order, *, number, code, issued_at, environment="homologation"):
    reasons = []
    recipient = _recipient(order, reasons)
    items, weights = _lines(company, order, recipient, reasons)
    if not items and not reasons:
        reasons.append("Pedido sem itens para a nota.")
    if reasons:
        return None, reasons
    profile = nfe_rules.PROFILES[company["crt"]]
    sums = nfe_xml.totals(items)
    if order.get("total") is not None and Decimal(nfe_xml.money(order["total"])) != sums["vNF"]:
        return None, [
            f"Total da nota ({sums['vNF']}) difere do pedido ({order['total']}); confira descontos."
        ]
    spec = {
        "company": company,
        "recipient": recipient,
        "items": items,
        "environment": environment,
        "series": company["series"],
        "number": number,
        "code": code,
        "issued_at": issued_at,
        "nature": profile["nature"],
        "presence": profile["presence"],
        "freight_mode": (order.get("transport") or {}).get("freight_mode")
        or profile["freight_mode"],
        "software": SOFTWARE,
        "payments": [{"type": profile["payment"], "description": "Outros", "amount": sums["vNF"]}]
        if profile["payment"] == "99"
        else [{"type": profile["payment"], "amount": sums["vNF"]}],
        "transport": _order_transport(order, weights),
        "intermediary": "1" if order.get("intermediary") else "0",
        "intermediary_info": order.get("intermediary"),
        "notes": order.get("notes") or "",
        "technical": company["technical"],
    }
    return spec, []


def _history(erp, db, invoice_id, version, status, detail, key):
    db.execute(
        "INSERT INTO fiscal_invoice_history VALUES (?,?,?,?,?,?)",
        (invoice_id, version, status, dumps(detail), key, erp.now()),
    )


RETURN_CFOP = {"5": "1202", "6": "2202"}
RETURN_CFOP_ST = {"5403": "1411", "5405": "1411", "6403": "2411", "6404": "2411"}
RETURN_ACTIVE = "('draft','sending','authorized')"


def _returned_so_far(db, invoice_id):
    done = {}
    for row in db.execute(
        f"SELECT data FROM fiscal_invoices WHERE status IN {RETURN_ACTIVE} "  # noqa: S608
        "AND json_extract(data,'$.purpose')='return' AND json_extract(data,'$.original_id')=?",
        (invoice_id,),
    ):
        for line in json.loads(row[0])["lines"]:
            done[line["line"]] = done.get(line["line"], Decimal(0)) + Decimal(line["quantity"])
    return done


def _return_order(order, chosen):
    """The part of the sale coming back, with its share of discount and freight."""
    items, discount, freight = [], Decimal(0), Decimal(0)
    rows = order["items"]
    gross = [Decimal(i["quantity"]) * Decimal(i["unit_price"]) for i in rows]
    discounts = _share(order.get("discount"), gross)
    freights = _share(order.get("freight"), gross)
    for line, quantity in chosen:
        item = rows[line]
        items.append({**item, "quantity": str(quantity)})
        part = quantity / Decimal(item["quantity"])
        discount += discounts[line] * part
        freight += freights[line] * part
    cents = Decimal("0.01")
    return {
        **order,
        "items": items,
        "discount": str(discount.quantize(cents, rounding=ROUND_HALF_UP)),
        "freight": str(freight.quantize(cents, rounding=ROUND_HALF_UP)),
        "total": None,
    }


def _prepare_return(erp, db, key, p):
    """Entry note for goods coming back, tied to the original. Fiscal only: stock has its own flow.

    Tax follows the same profile as the sale. CFOP and treatment per regime need the accountant's
    confirmation and a homologation run before real use.
    """
    if "invoice_id" not in p or set(p) - {"invoice_id", "lines"}:
        raise ERPError("fiscal_invoice", "Informe a nota original.")
    original = _invoice(db, p["invoice_id"])
    detail = json.loads(original["data"])
    if original["status"] != "authorized" or detail.get("purpose") == "return":
        raise ERPError("fiscal_return_state", "Só nota de venda autorizada aceita devolução.", 409)
    doc, order = erp.document(db, original["order_id"], "order")
    done = _returned_so_far(db, original["id"])
    raw = p.get("lines") or [
        {"line": index, "quantity": str(Decimal(item["quantity"]) - done.get(index, Decimal(0)))}
        for index, item in enumerate(order["items"])
        if Decimal(item["quantity"]) > done.get(index, Decimal(0))
    ]
    if not isinstance(raw, list) or not raw:
        raise ERPError("fiscal_return_lines", "Esta nota já foi devolvida por inteiro.", 409)
    chosen = []
    for row in raw:
        if not isinstance(row, dict) or set(row) != {"line", "quantity"}:
            raise ERPError("fiscal_return_lines", "Linha de devolução inválida.")
        line = row["line"]
        if type(line) is not int or not 0 <= line < len(order["items"]):
            raise ERPError("fiscal_return_lines", "Linha de devolução inválida.")
        quantity = Decimal(str(row["quantity"]))
        left = Decimal(order["items"][line]["quantity"]) - done.get(line, Decimal(0))
        if quantity <= 0 or quantity > left:
            raise ERPError("fiscal_return_quantity", "Quantidade maior que a vendida.", 409)
        chosen.append((line, quantity))
    company_row = db.execute(
        "SELECT * FROM fiscal_companies WHERE id=?", (original["company_id"],)
    ).fetchone()
    company = json.loads(company_row["data"])
    number, series = company["next_number"], company["series"]
    issued = datetime.now(UTC).astimezone(SAO_PAULO).isoformat(timespec="seconds")
    code = f"{secrets.randbelow(99999998) + 1:08d}"
    if int(code) == number:
        code = f"{(int(code) + 1) % 99999999 or 1:08d}"
    spec, reasons = build_spec(
        company, _return_order(order, chosen), number=number, code=code, issued_at=issued
    )
    if reasons:
        raise ERPError("fiscal_invoice_incomplete", " ".join(reasons[:8]))
    for item in spec["items"]:
        item["tax"].pop("difal", None)
        item["cfop"] = RETURN_CFOP_ST.get(item["cfop"]) or RETURN_CFOP[item["cfop"][0]]
    spec.update(
        nature="Devolucao de venda",
        direction="0",
        purpose="4",
        presence="0",
        intermediary=None,
        reference=original["access_key"],
        freight_mode="9",
        payments=[{"type": "90", "amount": "0.00"}],
        notes=f"Devolucao referente a NF-e {original['access_key']}",
    )
    built = nfe_xml.build(spec)
    data = {
        "number": number,
        "series": series,
        "key": built["key"],
        "total": built["totals"]["vNF"],
        "recipient": spec["recipient"]["name"],
        "environment": "homologation",
        "company": company["name"],
        "purpose": "return",
        "original_id": original["id"],
        "reference": original["access_key"],
        "lines": [{"line": line, "quantity": str(quantity)} for line, quantity in chosen],
    }
    identifier = str(uuid4())
    db.execute(
        "INSERT INTO fiscal_invoices VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            identifier,
            doc["id"],
            original["company_id"],
            series,
            number,
            built["key"],
            "draft",
            1,
            dumps(data),
            built["xml"],
            None,
            None,
            erp.now(),
        ),
    )
    company["next_number"] = number + 1
    db.execute(
        "UPDATE fiscal_companies SET data=?,updated_at=? WHERE id=?",
        (dumps(company), erp.now(), original["company_id"]),
    )
    _history(erp, db, identifier, 1, "draft", data, key)
    return {"id": identifier, "key": built["key"], "total": built["totals"]["vNF"]}


def _prepare(erp, db, key, p):
    if "order_id" not in p or set(p) - {"order_id", "company_id"}:
        raise ERPError("fiscal_invoice", "Informe pedido e empresa emissora.")
    doc, order = erp.document(db, p["order_id"], "order")
    if not p.get("company_id"):
        from app.erp_local import multi_company

        routed = multi_company.route_for_order(db, order)
        if not routed:
            raise ERPError("fiscal_route", "Loja sem empresa emissora definida.", 409)
        p = {**p, "company_id": routed}
    if doc["status"] in {"cancelled", "returned", "draft"}:
        raise ERPError("fiscal_order_status", "Pedido sem situação válida para emitir nota.", 409)
    row = db.execute("SELECT * FROM fiscal_companies WHERE id=?", (p["company_id"],)).fetchone()
    if not row:
        raise ERPError("fiscal_company", "Empresa emissora não encontrada.", 404)
    company = json.loads(row["data"])
    existing = db.execute(
        "SELECT * FROM fiscal_invoices WHERE order_id=? AND status IN "
        "('draft','sending','authorized','rejected') "
        "AND COALESCE(json_extract(data,'$.purpose'),'')!='return' ORDER BY rowid DESC",
        (doc["id"],),
    ).fetchone()
    if existing and existing["status"] in {"sending", "authorized"}:
        raise ERPError("fiscal_invoice_exists", "O pedido já tem nota enviada ou autorizada.", 409)
    if existing and existing["company_id"] != p["company_id"]:
        raise ERPError(
            "fiscal_invoice_exists", "Conclua a nota da outra empresa antes de trocar.", 409
        )
    if existing:
        number, series = existing["number"], existing["series"]
    else:
        number, series = company["next_number"], company["series"]
    issued = datetime.now(UTC).astimezone(SAO_PAULO).isoformat(timespec="seconds")
    code = f"{secrets.randbelow(99999998) + 1:08d}"
    if int(code) == number:
        code = f"{(int(code) + 1) % 99999999 or 1:08d}"
    spec, reasons = build_spec(company, order, number=number, code=code, issued_at=issued)
    if reasons:
        raise ERPError("fiscal_invoice_incomplete", " ".join(reasons[:8]))
    built = nfe_xml.build(spec)
    detail = {
        "number": number,
        "series": series,
        "key": built["key"],
        "total": built["totals"]["vNF"],
        "recipient": spec["recipient"]["name"],
        "environment": "homologation",
        "company": company["name"],
    }
    if existing:
        version = existing["version"] + 1
        db.execute(
            "UPDATE fiscal_invoices SET access_key=?,status='draft',version=?,data=?,xml=?,"
            "signed_xml=NULL,updated_at=? WHERE id=?",
            (built["key"], version, dumps(detail), built["xml"], erp.now(), existing["id"]),
        )
        identifier = existing["id"]
    else:
        identifier, version = str(uuid4()), 1
        db.execute(
            "INSERT INTO fiscal_invoices VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                identifier,
                doc["id"],
                p["company_id"],
                series,
                number,
                built["key"],
                "draft",
                1,
                dumps(detail),
                built["xml"],
                None,
                None,
                erp.now(),
            ),
        )
        company["next_number"] = number + 1
        db.execute(
            "UPDATE fiscal_companies SET data=?,updated_at=? WHERE id=?",
            (dumps(company), erp.now(), p["company_id"]),
        )
    _history(erp, db, identifier, version, "draft", detail, key)
    return {
        "id": identifier,
        "number": number,
        "access_key": built["key"],
        "status": "draft",
        "external_writes": False,
    }


def _invoice(db, identifier):
    row = db.execute("SELECT * FROM fiscal_invoices WHERE id=?", (identifier,)).fetchone()
    if not row:
        raise ERPError("fiscal_invoice", "Nota não encontrada.", 404)
    return row


def _mark_sending(erp, db, key, p):
    if set(p) != {"id", "signed_xml"} or not isinstance(p["signed_xml"], str):
        raise ERPError("fiscal_invoice", "Informe nota e XML assinado.")
    row = _invoice(db, p["id"])
    if row["status"] != "draft":
        raise ERPError("fiscal_invoice_state", "Só uma nota preparada pode ser enviada.", 409)
    if not nfe_xml.verify(p["signed_xml"]) or row["access_key"] not in p["signed_xml"]:
        raise ERPError("fiscal_signature", "A assinatura do XML não confere.")
    version = row["version"] + 1
    db.execute(
        "UPDATE fiscal_invoices SET status='sending',signed_xml=?,version=?,updated_at=? "
        "WHERE id=?",
        (p["signed_xml"], version, erp.now(), row["id"]),
    )
    _history(erp, db, row["id"], version, "sending", {"key": row["access_key"]}, key)
    return {"id": row["id"], "status": "sending", "external_writes": False}


def _record(erp, db, key, p):
    if set(p) != {"id", "code", "message", "protocol", "protocol_xml", "outcome"}:
        raise ERPError("fiscal_invoice", "Resultado da SEFAZ incompleto.")
    row = _invoice(db, p["id"])
    if row["status"] != "sending":
        raise ERPError("fiscal_invoice_state", "A nota não está aguardando retorno.", 409)
    outcome = p["outcome"]
    if outcome not in {"authorized", "rejected", "denied", "pending"}:
        raise ERPError("fiscal_invoice_state", "Resultado da SEFAZ não reconhecido.")
    status = {"pending": "sending"}.get(outcome, outcome)
    final = None
    if outcome == "authorized":
        if not p["protocol_xml"] or not p["protocol"]:
            raise ERPError("fiscal_protocol", "Autorização sem protocolo não é aceita.")
        final = (
            f'<nfeProc versao="4.00" xmlns="{nfe_xml.NFE_NS}">{row["signed_xml"]}'
            f"{p['protocol_xml']}</nfeProc>"
        )
    data = json.loads(row["data"])
    data.update(sefaz_code=p["code"], sefaz_message=p["message"], protocol=p["protocol"])
    version = row["version"] + 1
    db.execute(
        "UPDATE fiscal_invoices SET status=?,version=?,data=?,authorized_xml=?,updated_at=? "
        "WHERE id=?",
        (status, version, dumps(data), final, erp.now(), row["id"]),
    )
    _history(
        erp,
        db,
        row["id"],
        version,
        status,
        {"code": p["code"], "message": p["message"], "protocol": p["protocol"]},
        key,
    )
    return {"id": row["id"], "status": status, "external_writes": False}


def _record_event(erp, db, key, p):
    needed = {"id", "kind", "sequence", "text", "code", "message", "protocol", "xml", "accepted"}
    if set(p) != needed or p["kind"] not in {"110111", "110110"}:
        raise ERPError("fiscal_event", "Resultado do evento incompleto.")
    row = _invoice(db, p["id"])
    if row["status"] != "authorized":
        raise ERPError("fiscal_event_state", "Evento só vale para nota autorizada.", 409)
    status = "accepted" if p["accepted"] else "rejected"
    db.execute(
        "INSERT OR REPLACE INTO fiscal_invoice_events VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            row["id"],
            p["kind"],
            p["sequence"],
            status,
            p["text"],
            p["protocol"],
            p["code"],
            p["message"],
            p["xml"],
            erp.now(),
        ),
    )
    new_status = row["status"]
    if p["kind"] == "110111" and p["accepted"]:
        new_status = "cancelled"
    version = row["version"] + 1
    data = json.loads(row["data"])
    data.update(sefaz_code=p["code"], sefaz_message=p["message"])
    db.execute(
        "UPDATE fiscal_invoices SET status=?,version=?,data=?,updated_at=? WHERE id=?",
        (new_status, version, dumps(data), erp.now(), row["id"]),
    )
    _history(
        erp,
        db,
        row["id"],
        version,
        new_status,
        {"event": p["kind"], "sequence": p["sequence"], "code": p["code"]},
        key,
    )
    return {"id": row["id"], "status": new_status, "accepted": p["accepted"]}


def _record_gap(erp, db, p):
    needed = {
        "company_id",
        "series",
        "first",
        "last",
        "reason",
        "code",
        "message",
        "protocol",
        "accepted",
    }
    if set(p) != needed:
        raise ERPError("fiscal_gap", "Resultado da inutilização incompleto.")
    identifier = str(uuid4())
    db.execute(
        "INSERT INTO fiscal_number_gaps VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            identifier,
            p["company_id"],
            p["series"],
            p["first"],
            p["last"],
            p["reason"],
            "accepted" if p["accepted"] else "rejected",
            p["protocol"],
            p["code"],
            p["message"],
            erp.now(),
        ),
    )
    return {"id": identifier, "accepted": p["accepted"], "external_writes": False}


def apply(erp, db, key, kind, p):
    if not isinstance(p, dict):
        raise ERPError("fiscal_invoice", "Dados da nota não reconhecidos.")
    if kind == "fiscal.company.save":
        return _save_company(erp, db, p)
    if kind == "fiscal.invoice.prepare":
        return _prepare(erp, db, key, p)
    if kind == "fiscal.invoice.prepare_return":
        return _prepare_return(erp, db, key, p)
    if kind == "fiscal.invoice.mark_sending":
        return _mark_sending(erp, db, key, p)
    if kind == "fiscal.invoice.event_record":
        return _record_event(erp, db, key, p)
    if kind == "fiscal.gap.record":
        return _record_gap(erp, db, p)
    return _record(erp, db, key, p)


def transmit(erp, identifier, sefaz, certificate, password):
    """Send or resolve one note. An uncertain send is always consulted before any resend."""
    with erp.connection() as db:
        row = _invoice(db, identifier)
    if row["status"] == "authorized":
        return {"id": identifier, "status": "authorized", "duplicate": True}
    if row["status"] == "sending":
        result = sefaz.consult(row["access_key"])
        if result["code"] == "217":
            result = sefaz.authorize(row["signed_xml"])
    elif row["status"] == "draft":
        signed = nfe_xml.sign(row["xml"], certificate, password)
        erp.command(
            str(uuid4()), "fiscal.invoice.mark_sending", {"id": identifier, "signed_xml": signed}
        )
        result = sefaz.authorize(signed)
    else:
        raise ERPError("fiscal_invoice_state", "Prepare a nota novamente antes de enviar.", 409)
    if result["outcome"] == "consult":
        result = sefaz.consult(row["access_key"])
    return erp.command(
        str(uuid4()),
        "fiscal.invoice.record",
        {
            "id": identifier,
            "code": result["code"],
            "message": result["message"],
            "protocol": result.get("protocol"),
            "protocol_xml": result.get("protocol_xml"),
            "outcome": result["outcome"] if result["outcome"] != "consult" else "pending",
        },
    )


def _reason(raw, name):
    value = text(raw, name, 255 if name == "justificativa" else 1000)
    if len(value) < 15:
        raise ERPError("fiscal_reason", f"A {name} precisa de ao menos 15 caracteres.")
    return value


def _company_of(db, row):
    return json.loads(
        db.execute("SELECT data FROM fiscal_companies WHERE id=?", (row["company_id"],)).fetchone()[
            0
        ]
    )


def cancel(erp, identifier, justification, sefaz, certificate, password):
    """Cancel an authorized note. The SEFAZ decides the deadline; only its answer changes state."""
    justification = _reason(justification, "justificativa")
    with erp.connection() as db:
        row = _invoice(db, identifier)
        if row["status"] != "authorized":
            raise ERPError("fiscal_event_state", "Só nota autorizada pode ser cancelada.", 409)
        company = _company_of(db, row)
    protocol = json.loads(row["data"]).get("protocol")
    if not protocol:
        raise ERPError("fiscal_protocol", "Nota sem protocolo de autorização registrado.", 409)
    moment = datetime.now(UTC).astimezone(SAO_PAULO).isoformat(timespec="seconds")
    unsigned = nfe_xml.event(
        "110111",
        uf=company["address"]["uf"],
        cnpj=company["cnpj"],
        key=row["access_key"],
        moment=moment,
        sequence=1,
        protocol=protocol,
        text=justification,
    )
    signed = nfe_xml.sign_element(unsigned, "infEvento", "evento", certificate, password)
    result = sefaz.send_event(signed)
    return erp.command(
        str(uuid4()),
        "fiscal.invoice.event_record",
        {
            "id": identifier,
            "kind": "110111",
            "sequence": 1,
            "text": justification,
            "code": result["code"],
            "message": result["message"],
            "protocol": result["protocol"],
            "xml": result["event_xml"],
            "accepted": result["accepted"],
        },
    )


def correct(erp, identifier, correction, sefaz, certificate, password):
    """Correction letter. Each new one replaces the last at SEFAZ, with a growing sequence."""
    correction = _reason(correction, "correção")
    with erp.connection() as db:
        row = _invoice(db, identifier)
        if row["status"] != "authorized":
            raise ERPError("fiscal_event_state", "Só nota autorizada aceita correção.", 409)
        company = _company_of(db, row)
        used = db.execute(
            "SELECT max(sequence) FROM fiscal_invoice_events WHERE invoice_id=? AND kind='110110' "
            "AND status='accepted'",
            (identifier,),
        ).fetchone()[0]
    sequence = (used or 0) + 1
    if sequence > 20:
        raise ERPError("fiscal_event_limit", "Limite de 20 cartas de correção atingido.", 409)
    moment = datetime.now(UTC).astimezone(SAO_PAULO).isoformat(timespec="seconds")
    unsigned = nfe_xml.event(
        "110110",
        uf=company["address"]["uf"],
        cnpj=company["cnpj"],
        key=row["access_key"],
        moment=moment,
        sequence=sequence,
        text=correction,
    )
    signed = nfe_xml.sign_element(unsigned, "infEvento", "evento", certificate, password)
    result = sefaz.send_event(signed)
    return erp.command(
        str(uuid4()),
        "fiscal.invoice.event_record",
        {
            "id": identifier,
            "kind": "110110",
            "sequence": sequence,
            "text": correction,
            "code": result["code"],
            "message": result["message"],
            "protocol": result["protocol"],
            "xml": result["event_xml"],
            "accepted": result["accepted"],
        },
    )


def void_numbers(erp, company_id, first, last, reason, sefaz, certificate, password, *, year=None):
    """Void numbers no note used. A range touching a real note is refused before any send."""
    reason = _reason(reason, "justificativa")
    if type(first) is not int or type(last) is not int or not 1 <= first <= last:
        raise ERPError("fiscal_gap", "Informe uma faixa de números válida.")
    with erp.connection() as db:
        row = db.execute("SELECT data FROM fiscal_companies WHERE id=?", (company_id,)).fetchone()
        if not row:
            raise ERPError("fiscal_company", "Empresa emissora não encontrada.", 404)
        company = json.loads(row[0])
        used = db.execute(
            "SELECT number FROM fiscal_invoices WHERE company_id=? AND series=? "
            "AND number BETWEEN ? AND ? AND status<>'rejected'",
            (company_id, company["series"], first, last),
        ).fetchone()
    if used:
        raise ERPError("fiscal_gap_used", f"O número {used[0]} já pertence a uma nota.", 409)
    unsigned = nfe_xml.number_gap(
        uf=company["address"]["uf"],
        year=year or datetime.now(UTC).astimezone(SAO_PAULO).year,
        cnpj=company["cnpj"],
        series=company["series"],
        first=first,
        last=last,
        reason=reason,
    )
    signed = nfe_xml.sign_element(unsigned, "infInut", "inutNFe", certificate, password)
    result = sefaz.void_numbers(signed)
    return erp.command(
        str(uuid4()),
        "fiscal.gap.record",
        {
            "company_id": company_id,
            "series": company["series"],
            "first": first,
            "last": last,
            "reason": reason,
            "code": result["code"],
            "message": result["message"],
            "protocol": result["protocol"],
            "accepted": result["accepted"],
        },
    )


def snapshot(db):
    return {
        "companies": [
            {"id": r["id"], "version": r["version"], "data": json.loads(r["data"])}
            for r in db.execute("SELECT * FROM fiscal_companies ORDER BY updated_at")
        ],
        "invoices": [
            {
                "id": r["id"],
                "order_id": r["order_id"],
                "company_id": r["company_id"],
                "status": r["status"],
                "version": r["version"],
                "access_key": r["access_key"],
                "updated_at": r["updated_at"],
                "data": json.loads(r["data"]),
            }
            for r in db.execute("SELECT * FROM fiscal_invoices ORDER BY rowid DESC LIMIT 500")
        ],
    }


def _order_transport(order, weights):
    """Volumes, weight and carrier typed on the order win over the product weights."""
    typed = order.get("transport") or {}
    out = {"net_weight": weights[0], "gross_weight": typed.get("gross_weight") or weights[1]}
    if typed.get("volumes"):
        out["volumes"] = typed["volumes"]
    if typed.get("carrier") and typed.get("carrier_cnpj"):
        out["carrier"] = {"cnpj": typed["carrier_cnpj"], "name": typed["carrier"]}
    return out
