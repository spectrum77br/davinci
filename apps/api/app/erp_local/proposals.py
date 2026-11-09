"""Commercial proposals and revisions. Quoting never reserves stock or cash.

Generate sale only pre-fills the sales screen. The first actual order.save links
and locks the proposal in the same transaction as saving that order.
"""

import json
from decimal import Decimal
from uuid import uuid4

from app.erp_local.core import ERPError, dumps, number, text, total
from app.erp_local.finance import day, today

SCHEMA = """
CREATE TABLE IF NOT EXISTS commercial_proposals (
 id TEXT PRIMARY KEY, external_key TEXT UNIQUE NOT NULL, status TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS commercial_proposal_history (
 proposal_id TEXT REFERENCES commercial_proposals(id), version INTEGER NOT NULL,
 data TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(proposal_id,version));
"""
COMMANDS = {"proposal.save", "proposal.revise", "proposal.situation", "proposal.batch"}
TEXT_FIELDS = {
    "attention",
    "introduction",
    "other_description",
    "commercial_text",
    "delivery_terms",
    "warranty",
    "carrier_name",
    "notes",
    "internal_notes",
    "greeting",
    "department",
}
SAVE_FIELDS = {
    "id",
    "version",
    "number",
    "channel",
    "store",
    "date",
    "next_contact",
    "valid_until",
    "customer_name",
    "contact_id",
    "refresh_contact",
    "items",
    "other_total",
    "freight",
    "discount",
    "charges",
    "payment_method_id",
    "price_list_id",
    "price_quote_token",
    "seller_id",
    "freight_payer",
    "packages",
    "weight_kg",
    *TEXT_FIELDS,
}
SITUATIONS = {"open", "accepted", "rejected", "cancelled"}


def get(db, identifier):
    row = db.execute("SELECT * FROM commercial_proposals WHERE id=?", (identifier,)).fetchone()
    if not row:
        raise ERPError("proposal_not_found", "Proposta comercial não encontrada.", 404)
    return row, json.loads(row["data"])


def check_version(data, payload):
    if type(payload.get("version", 0)) is not int or payload.get("version", 0) != data.get(
        "version", 0
    ):
        raise ERPError("proposal_version", "A proposta mudou. Atualize antes de continuar.", 409)


def persist(erp, db, identifier, status, data, external_key=None):
    if external_key is not None:
        db.execute(
            "INSERT INTO commercial_proposals VALUES (?,?,?,?)",
            (identifier, external_key, status, dumps(data)),
        )
    else:
        db.execute(
            "UPDATE commercial_proposals SET status=?,data=? WHERE id=?",
            (status, dumps(data), identifier),
        )
    db.execute(
        "INSERT INTO commercial_proposal_history VALUES (?,?,?,?)",
        (identifier, data["version"], dumps({**data, "status": status}), erp.now()),
    )


def apply(erp, db, kind, payload):
    from app.erp_local import contacts, payment_methods, price_lists, product_details, sellers

    if kind == "proposal.revise" and isinstance(payload, dict) and "items" in payload:
        if set(payload) - SAVE_FIELDS:
            raise ERPError("proposal_fields", "Campos da proposta não reconhecidos.")
        revised = apply(erp, db, kind, {"id": payload.get("id"), "version": payload.get("version")})
        return apply(erp, db, "proposal.save", {**payload, "version": revised["version"]})
    if kind == "proposal.batch":
        if (
            not isinstance(payload, dict)
            or set(payload) != {"entries", "status"}
            or not isinstance(payload["entries"], list)
            or not 1 <= len(payload["entries"]) <= 100
            or any(
                not isinstance(e, dict) or set(e) != {"id", "version"} for e in payload["entries"]
            )
        ):
            raise ERPError("proposal_batch", "Selecione até 100 propostas e a situação.")
        ids = [entry["id"] for entry in payload["entries"]]
        if len(set(ids)) != len(ids):
            raise ERPError("proposal_batch", "Não repita propostas no mesmo lote.")
        return {
            "proposals": [
                apply(erp, db, "proposal.situation", {**entry, "status": payload["status"]})
                for entry in payload["entries"]
            ]
        }
    allowed = SAVE_FIELDS if kind == "proposal.save" else {"id", "version", "status"}
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise ERPError("proposal_fields", "Campos da proposta não reconhecidos.")
    previous, old = get(db, payload["id"]) if payload.get("id") else (None, {})
    if kind != "proposal.save" and not previous:
        raise ERPError("proposal_not_found", "Selecione uma proposta.")
    check_version(old, payload)
    if old.get("linked_sale"):
        raise ERPError("proposal_locked", "A proposta já gerou venda e permanece protegida.", 409)
    identifier = previous["id"] if previous else str(uuid4())
    version = old.get("version", 0) + 1
    if kind == "proposal.situation":
        if payload.get("status") not in SITUATIONS:
            raise ERPError("proposal_status", "Escolha uma situação da proposta.")
        persist(erp, db, identifier, payload["status"], {**old, "version": version})
        return {"id": identifier, "version": version, "status": payload["status"]}
    if kind == "proposal.revise":
        if set(payload) - {"id", "version"}:
            raise ERPError("proposal_fields", "Informe somente a proposta e sua versão atual.")
        data = {**old, "version": version, "revision": old["revision"] + 1, "updated_at": erp.now()}
        persist(erp, db, identifier, "open", data)
        return {"id": identifier, "version": version, "revision": data["revision"]}
    if previous and previous["status"] not in {"open", "accepted"}:
        raise ERPError("proposal_edit", "Crie nova versão antes de editar proposta encerrada.", 409)
    identity = {k: text(payload.get(k), k) for k in ("number", "channel", "store")}
    if previous and any(old[k] != v for k, v in identity.items()):
        raise ERPError("proposal_identity", "Número, canal e loja identificam a proposta.", 409)
    external_key = dumps([identity[k] for k in ("channel", "store", "number")])
    if (
        not previous
        and db.execute(
            "SELECT 1 FROM commercial_proposals WHERE external_key=?", (external_key,)
        ).fetchone()
    ):
        raise ERPError("proposal_duplicate", "Já existe proposta com este número nesta loja.", 409)
    items = erp.items(db, payload["items"]) if payload.get("items") else []
    amounts = {
        k: number(payload.get(k, "0") or "0", money=True, zero=True)
        for k in ("freight", "discount", "charges", "other_total")
    }
    if not items and not amounts["other_total"]:
        raise ERPError("proposal_items", "Informe itens ou o valor dos outros itens/serviços.")
    subtotal = Decimal(total(items))
    gross = subtotal + amounts["other_total"] + amounts["freight"] + amounts["charges"]
    if amounts["discount"] > gross:
        raise ERPError("proposal_discount", "O desconto ultrapassa o total da proposta.")
    grand_total = number(gross - amounts["discount"], money=True, zero=True)
    name = text(payload.get("customer_name"), "cliente")
    frozen = None
    if payload.get("contact_id"):
        if "refresh_contact" in payload and type(payload["refresh_contact"]) is not bool:
            raise ERPError("proposal_contact", "Opção de atualizar contato inválida.")
        frozen = old.get("contact")
        if not frozen or frozen["id"] != payload["contact_id"] or payload.get("refresh_contact"):
            frozen = contacts.document_snapshot(db, payload["contact_id"])
        if frozen["name"] != name:
            raise ERPError("proposal_contact", "O nome difere do contato selecionado.", 409)
    data = {
        **identity,
        "version": version,
        "revision": old.get("revision", 1),
        "created_at": old.get("created_at", erp.now()),
        "updated_at": erp.now(),
        "customer_name": name,
        "items": items,
        "subtotal": str(subtotal),
        "total": str(grand_total),
        **{k: str(v) for k, v in amounts.items()},
        "product_snapshots": {
            i["sku"]: product_details.document_snapshot(erp, db, i["sku"]) for i in items
        },
    }
    data.update({k: text(payload[k], k, 2000) if payload.get(k) else "" for k in TEXT_FIELDS})
    data["date"] = day(payload.get("date") or today())
    for key in ("next_contact", "valid_until"):
        data[key] = day(payload[key]) if payload.get(key) else ""
    if data["valid_until"] and data["valid_until"] < data["date"]:
        raise ERPError("proposal_dates", "A validade deve alcançar a data da proposta.")
    if frozen:
        data["contact"] = frozen
    quote = price_lists.bind(
        erp,
        db,
        payload,
        old,
        items,
        {
            "date": data["date"],
            "contact_id": payload.get("contact_id") or "",
            "payment_method_id": payload.get("payment_method_id") or "",
            "state": (frozen or {}).get("state", ""),
            "channel": identity["channel"],
            "store": identity["store"],
        },
    )
    if quote:
        data["price_quote"] = quote
    seller = sellers.bind(db, payload.get("seller_id"), old, subtotal, amounts["discount"])
    if seller:
        data["seller"] = seller
    payment = old.get("payment_method")
    if payload.get("payment_method_id"):
        if not payment or payment["id"] != payload["payment_method_id"]:
            payment = payment_methods.freeze(db, payload["payment_method_id"])
        installments, forecasts = payment_methods.plan(payment, grand_total, data["date"])
        data.update(payment_method=payment, installments=installments, payment_forecasts=forecasts)
    data["freight_payer"] = payload.get("freight_payer", "0")
    if data["freight_payer"] not in {"0", "1", "2", "3", "4", "9"}:
        raise ERPError("proposal_freight", "Modalidade de frete inválida.")
    packages = payload.get("packages", 0)
    if type(packages) is not int or not 0 <= packages <= 9999:
        raise ERPError("proposal_packages", "Quantidade de volumes inválida.")
    data["packages"] = packages
    data["weight_kg"] = str(number(payload.get("weight_kg", "0") or "0", zero=True))
    status = previous["status"] if previous else "open"
    persist(erp, db, identifier, status, data, external_key if not previous else None)
    return {
        "id": identifier,
        "version": version,
        "revision": data["revision"],
        "status": status,
        "external_writes": False,
    }


def sale_source(db, payload, previous):
    if not payload.get("proposal_id"):
        if payload.get("proposal_version") is not None:
            raise ERPError("proposal_source", "Selecione a proposta de origem.")
        return None
    if previous:
        raise ERPError("proposal_source", "A origem é vinculada somente ao criar a venda.", 409)
    row, data = get(db, payload["proposal_id"])
    check_version(data, {"version": payload.get("proposal_version")})
    if data.get("linked_sale") or row["status"] not in {"open", "accepted"}:
        raise ERPError("proposal_source", "A proposta não está disponível para gerar venda.", 409)
    if Decimal(data["other_total"]):
        raise ERPError(
            "proposal_services",
            "A conversão de itens/serviços não cadastrados "
            "ainda exige implementação própria; a proposta permanece salva.",
            409,
        )
    if data["valid_until"] and day(payload.get("date") or today()) > data["valid_until"]:
        raise ERPError(
            "proposal_expired", "Revise a validade da proposta antes de gerar venda.", 409
        )
    return {"id": row["id"], **data}


def link_sale(erp, db, source, sale_id):
    if not source:
        return
    identifier = source["id"]
    data = {k: v for k, v in source.items() if k != "id"}
    data.update(linked_sale=sale_id, version=data["version"] + 1, updated_at=erp.now())
    persist(erp, db, identifier, "converted", data)


def snapshot(db):
    return [
        {
            **dict(row),
            "data": json.loads(row["data"]),
            "history": [
                {
                    "version": h["version"],
                    "data": json.loads(h["data"]),
                    "created_at": h["created_at"],
                }
                for h in db.execute(
                    "SELECT * FROM commercial_proposal_history WHERE proposal_id=? "
                    "ORDER BY version DESC",
                    (row["id"],),
                )
            ],
        }
        for row in db.execute("SELECT * FROM commercial_proposals ORDER BY rowid DESC")
    ]


def print_html(db, identifier):
    from html import escape

    row, data = get(db, identifier)
    e = lambda value: escape(str(value))  # noqa: E731 - bounded HTML text escaping

    def decimal_br(value, places=2):
        return f"{Decimal(value):,.{places}f}".replace(",", "_").replace(".", ",").replace("_", ".")

    def date_br(value):
        return "/".join(value.split("-")[::-1]) if value else ""

    status = {
        "open": "Em aberto",
        "accepted": "Aprovada",
        "rejected": "Não aprovada",
        "cancelled": "Cancelada",
        "converted": "Venda gerada",
    }[row["status"]]
    freight = {
        "0": "Remetente (CIF)",
        "1": "Destinatário (FOB)",
        "2": "Terceiros",
        "3": "Transporte próprio do remetente",
        "4": "Transporte próprio do destinatário",
        "9": "Sem ocorrência de transporte",
    }[data["freight_payer"]]
    lines = "".join(
        "<tr>"
        + "".join(
            "<td>" + e(v) + "</td>"
            for v in [
                data["product_snapshots"].get(i["sku"], {}).get("name", i["sku"]),
                i["sku"],
                decimal_br(i["quantity"], 3),
                decimal_br(i["unit_price"], 4),
                decimal_br(total([i])),
            ]
        )
        + "</tr>"
        for i in data["items"]
    )
    fields = [
        ("Cliente", data["customer_name"]),
        ("Aos cuidados de", data["attention"]),
        ("Data", date_br(data["date"])),
        ("Introdução", data["introduction"]),
        ("Outros itens ou serviços", data["other_description"]),
        ("Total outros itens", decimal_br(data["other_total"])),
        ("Frete", decimal_br(data["freight"])),
        ("Outras despesas", decimal_br(data["charges"])),
        ("Desconto", decimal_br(data["discount"])),
        ("Total da proposta (R$)", decimal_br(data["total"])),
        ("Condições comerciais", data["commercial_text"]),
        ("Validade", date_br(data["valid_until"])),
        ("Prazo de entrega", data["delivery_terms"]),
        ("Garantia", data["warranty"]),
        ("Transportador", data["carrier_name"]),
        ("Frete por conta", freight),
        ("Observações", data["notes"]),
        ("Saudação", data["greeting"]),
        ("Departamento", data["department"]),
    ]
    conditions = "".join(
        "<p><strong>" + e(label) + ":</strong> " + e(value) + "</p>"
        for label, value in fields
        if value
    )
    installments = "".join(
        "<li>" + e(date_br(i["due_date"])) + " · R$ " + e(decimal_br(i["amount"])) + "</li>"
        for i in data.get("installments", [])
    )
    return (
        '<!doctype html><html lang="pt-BR"><meta charset="utf-8">'
        '<link rel="stylesheet" href="/assets/print.css"><title>DaVinci · Proposta '
        + e(data["number"])
        + "</title><h1>DaVinci · Proposta "
        + e(data["number"])
        + "</h1><p>Revisão "
        + e(data["revision"])
        + " · "
        + e(status)
        + "</p><p>Documento comercial da base própria.</p>"
        + conditions
        + "<table><thead><tr><th>Item</th><th>Código</th><th>Quantidade</th>"
        "<th>Preço unitário (R$)</th><th>Total (R$)</th></tr></thead><tbody>"
        + lines
        + "</tbody></table><ul>"
        + installments
        + "</ul></html>"
    )
