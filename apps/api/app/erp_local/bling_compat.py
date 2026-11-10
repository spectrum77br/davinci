"""Bling v3-shaped API over the own ERP, for DaVinci services that keep their Bling client.

The switch is the client's base URL. Ids already stored in DaVinci continue (catalogue rows keep
their origin id); new records get ids above Bling's range. Every write goes through the normal
ERP commands, so events, versions and stock rules stay the same. Situations that have no stock
meaning are labels here: moving an order to them never moves stock.
"""

import hashlib
import hmac
import json
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse, Response

from app.erp_local import categories, contacts, product_details, product_structures
from app.erp_local.core import ERPError, text

SCHEMA = """
CREATE TABLE IF NOT EXISTS compat_ids (
 kind TEXT NOT NULL, local_id TEXT NOT NULL, numeric_id INTEGER NOT NULL,
 PRIMARY KEY(kind,local_id), UNIQUE(kind,numeric_id));
CREATE TABLE IF NOT EXISTS compat_outbox (
 id INTEGER PRIMARY KEY AUTOINCREMENT, event TEXT NOT NULL, body TEXT NOT NULL,
 created_at TEXT NOT NULL, delivered_at TEXT, attempts INTEGER NOT NULL DEFAULT 0,
 next_attempt_at TEXT);
CREATE TABLE IF NOT EXISTS compat_order_situations (
 order_id TEXT PRIMARY KEY, situation_id INTEGER NOT NULL, operation_key TEXT NOT NULL,
 updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS order_occurrences (
 id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT NOT NULL, operation_key TEXT NOT NULL,
 occurred_at TEXT NOT NULL, text TEXT NOT NULL, situation_id INTEGER, actor TEXT NOT NULL,
 created_at TEXT NOT NULL, removed_at TEXT, removed_by TEXT);
"""
FIRST_OWN_ID = 900_000_000_000
# Own orders are numbered from 900.001: Bling keeps numbering the stores it still runs (308 mil
# in 11/2026, a few thousand a month), and DaVinci keys chamados, labels and notes by number.
OWN_NUMBER_START = 900_000
TOKEN_ENV = "ERP_COMPAT_TOKEN"  # noqa: S105 - environment variable name
MODULE_SALES = 98310
SITUATIONS = {
    6: "Em aberto",
    9: "Atendido",
    12: "Cancelado",
    15: "Em andamento",
    21: "Em digitação",
    83953: "Entregue",
    83955: "Aguardando Cancelamento",
    83956: "Perdimento",
    83957: "Aguardando Devolução",
    83958: "Enviado Fake",
    83959: "Enviado Geral PI",
    83960: "Problemas",
    83962: "Verificar Cancelamento",
    83963: "Devolvido Estoque",
    83965: "Enviado Etiqueta",
    83966: "Erro no Envio",
    84674: "Enviado Geral SP",
    84675: "Enviado Geral CI",
    84676: "Devolvido Estoque Usado",
    84677: "Manutenção",
    84678: "Enviado Geral SA",
    84680: "Verificar Margem",
    84686: "Golpe",
    545901: "Sucata",
    545902: "Resolvido",
}
DERIVED_SITUATION = {"draft": 6, "reserved": 6, "shipped": 9, "cancelled": 12, "returned": 83963}
UNIT_TO_BLING = {"m": 0, "cm": 1, "mm": 2}
CONDITION_TO_BLING = {"unspecified": 0, "new": 1, "used": 2, "refurbished": 3}
PRODUCTION_TO_BLING = {"own": "P", "third_party": "T", "": "P"}


def _error(status, message, *, code=None, field_code=None):
    body = {"error": {"type": code or "VALIDATION_ERROR", "message": message}}
    if field_code is not None:
        body["error"]["fields"] = [{"code": field_code, "msg": message}]
    return JSONResponse(body, status_code=status)


def numeric_id(db, kind, local_id):
    if kind == "product":
        origin = db.execute("SELECT data FROM catalog_origin WHERE sku=?", (local_id,)).fetchone()
        if origin:
            source = json.loads(origin[0]).get("bling_product_id")
            if source:
                return int(source)
    row = db.execute(
        "SELECT numeric_id FROM compat_ids WHERE kind=? AND local_id=?", (kind, local_id)
    ).fetchone()
    if row:
        return row[0]
    top = db.execute("SELECT max(numeric_id) FROM compat_ids WHERE kind=?", (kind,)).fetchone()[0]
    value = max(top or 0, FIRST_OWN_ID) + 1
    db.execute("INSERT INTO compat_ids VALUES (?,?,?)", (kind, local_id, value))
    return value


def local_id(db, kind, value):
    if value is None:
        return None
    if kind == "product":
        for row in db.execute("SELECT sku,data FROM catalog_origin"):
            if str(json.loads(row["data"]).get("bling_product_id")) == str(value):
                return row["sku"]
    row = db.execute(
        "SELECT local_id FROM compat_ids WHERE kind=? AND numeric_id=?", (kind, int(value))
    ).fetchone()
    return row[0] if row else None


def _float(value):
    return float(value) if value not in (None, "") else 0.0


def _situation_letter(status):
    return {"active": "A", "inactive": "I", "deleted": "E"}.get(status, "A")


def _available(product):
    # Inactive or excluded: nothing for sale, so the listings fed from here get 0.
    # The physical balance stays in saldoFisico.
    if product["details"]["status"] != "active":
        return 0.0
    rows = [b for b in product["balances"] if b.get("in_total") is not False]
    if not rows or any(b["available"] is None for b in rows):
        return 0.0
    return float(sum(Decimal(str(b["available"])) for b in rows))


def _format_letter(product):
    return "E" if product["kind"] == "kit" or product["structure"]["components"] else "S"


def _product_base(db, product):
    d = product["details"]
    images = product["images"]["rows"]
    return {
        "id": numeric_id(db, "product", product["sku"]),
        "nome": product["name"],
        "codigo": product["sku"],
        "preco": _float(product["price"]),
        "precoCusto": _float(product["cost_price"]),
        "estoque": {"saldoVirtualTotal": _available(product)},
        "tipo": "S" if product["kind"] == "service" else "P",
        "situacao": _situation_letter(d["status"]),
        "formato": _format_letter(product),
        "descricaoCurta": d["short_description_html"],
        "imagemURL": images[0]["url"] if images else "",
    }


def _product_detail(db, product):
    d, tax = product["details"], product["tax"]["data"]
    base = _product_base(db, product)
    unit = d["dimension_unit"]
    places = {"m": Decimal("0.01"), "cm": Decimal(1), "mm": Decimal(10)}[unit]

    def dimension(value):
        return float(Decimal(value) * places) if value else 0.0

    default_supplier = next(
        (r for r in product["suppliers"]["rows"] if r["is_default"]),
        None,
    )
    structure = product["structure"]
    return {
        **base,
        "unidade": d["unit"],
        "pesoLiquido": _float(d["net_weight_kg"]),
        "pesoBruto": _float(d["gross_weight_kg"]),
        "volumes": d["volumes"] or 0,
        "itensPorCaixa": _float(d["items_per_box"]),
        "gtin": d["gtin"],
        "gtinEmbalagem": d["gtin_tax"],
        "tipoProducao": PRODUCTION_TO_BLING[d["production"]],
        "condicao": CONDITION_TO_BLING[d["condition"]],
        "freteGratis": bool(d["free_shipping"]),
        "marca": d["brand"],
        "descricaoComplementar": d["complementary_html"],
        "linkExterno": d["external_link"],
        "observacoes": d["notes"],
        "dataValidade": d["expiry_date"] or "0000-00-00",
        "departamento": d["department"],
        "categoria": {
            "id": numeric_id(db, "category", product["category"]["id"])
            if product["category"]["id"]
            else 0
        },
        "estoque": {
            **base["estoque"],
            "minimo": _float(d["stock_min"]),
            "maximo": _float(d["stock_max"]),
            "crossdocking": d["crossdocking"] or 0,
            "localizacao": d["location"],
        },
        "fornecedor": {
            "id": numeric_id(
                db, "supplier_link", product["sku"] + ":" + default_supplier["contact_id"]
            ),
            "contato": {
                "id": numeric_id(db, "contact", default_supplier["contact_id"]),
                "nome": default_supplier["contact"]["name"],
            },
            "codigo": default_supplier["code"],
            "precoCusto": _float(default_supplier["cost_price"]),
            "precoCompra": _float(default_supplier["purchase_price"]),
        }
        if default_supplier
        else {
            "id": 0,
            "contato": {"id": 0, "nome": ""},
            "codigo": "",
            "precoCusto": 0.0,
            "precoCompra": 0.0,
        },
        "dimensoes": {
            "largura": dimension(d["width_cm"]),
            "altura": dimension(d["height_cm"]),
            "profundidade": dimension(d["length_cm"]),
            "unidadeMedida": UNIT_TO_BLING[unit],
        },
        "tributacao": {
            "origem": int(tax["origin"]) if tax.get("origin") not in (None, "") else 0,
            "ncm": tax.get("ncm", ""),
            "cest": tax.get("cest", ""),
            "codigoListaServicos": tax.get("service_code", ""),
            "spedTipoItem": tax.get("item_type", ""),
            "percentualTributos": _float(tax.get("approximate_tax_percent")),
            "valorBaseStRetencao": _float(tax.get("icms_st_retention_base")),
            "valorStRetencao": _float(tax.get("icms_st_retention_value")),
            "valorICMSSubstituto": _float(tax.get("icms_substitute_value")),
            "codigoExcecaoTipi": tax.get("tipi_exception", ""),
            "valorPisFixo": _float(tax.get("pis_fixed_value")),
            "valorCofinsFixo": _float(tax.get("cofins_fixed_value")),
            "dadosAdicionais": tax.get("additional_info", ""),
        },
        "midia": {
            "video": {"url": d["video_url"]},
            "imagens": {
                "externas": [],
                "internas": [
                    {"link": row["url"], "ordem": index}
                    for index, row in enumerate(product["images"]["rows"])
                ],
            },
        },
        "estrutura": {
            "tipoEstoque": "V" if product["kind"] == "kit" else "F",
            "lancamentoEstoque": "P" if product["kind"] == "kit" else "M",
            "componentes": [
                {
                    "produto": {"id": numeric_id(db, "product", part["sku"])},
                    "quantidade": float(part["quantity"]),
                }
                for part in structure["components"]
            ],
        },
        "variacoes": [],
    }


def _current_situation(db, doc):
    row = db.execute(
        "SELECT situation_id FROM compat_order_situations WHERE order_id=?", (doc["id"],)
    ).fetchone()
    return row[0] if row else DERIVED_SITUATION.get(doc["status"], 6)


def _store_id(erp, db, data):
    row = db.execute(
        "SELECT data FROM store_policies WHERE channel=? AND store=?",
        (data.get("channel"), data.get("store")),
    ).fetchone()
    if row:
        profile = json.loads(row[0]).get("source_profile") or {}
        if profile.get("bling_store_id"):
            return int(profile["bling_store_id"])
    return 0


def _contact_view(db, data):
    contact = data.get("contact") or {}
    return {
        "id": numeric_id(db, "contact", contact["id"]) if contact.get("id") else 0,
        "nome": contact.get("name") or data.get("customer_name", ""),
        "tipoPessoa": "J" if len(re.sub(r"\D", "", str(contact.get("document", "")))) > 11 else "F",
        "numeroDocumento": contact.get("document", ""),
    }


def _delivery(data):
    """The label shows who receives; it may differ from the buyer on the invoice."""
    source = data.get("recipient") or {}
    contact = data.get("contact") or {}
    pick = lambda key, other: source.get(key) or contact.get(other) or ""  # noqa: E731
    return {
        "nome": pick("name", "name"),
        "endereco": pick("street", "street"),
        "numero": pick("number", "number"),
        "complemento": pick("complement", "complement"),
        "municipio": pick("city", "city"),
        "uf": pick("state", "state"),
        "cep": pick("postal_code", "postal_code"),
        "bairro": pick("district", "district"),
    }


def _order_base(erp, db, doc):
    data = doc["data"]
    number_id = numeric_id(db, "order", doc["id"])
    return {
        "id": number_id,
        "numero": own_number(number_id),
        "numeroLoja": data.get("external_id", ""),
        "data": data.get("date") or str(data.get("created_at", ""))[:10] or "0000-00-00",
        "dataSaida": data.get("shipped_on", "0000-00-00"),
        "dataPrevista": data.get("expected_date", "0000-00-00"),
        "totalProdutos": _float(data.get("subtotal")),
        "total": _float(data.get("total")),
        "contato": _contact_view(db, data),
        "situacao": {"id": _current_situation(db, doc), "valor": 1},
        "loja": {"id": _store_id(erp, db, data)},
    }


def _invoice_of(db, order_id):
    row = db.execute(
        "SELECT id,access_key,status FROM fiscal_invoices WHERE order_id=? "
        "AND status='authorized' ORDER BY rowid DESC",
        (order_id,),
    ).fetchone()
    return row


def _order_detail(erp, db, doc):
    data = doc["data"]
    snapshots = data.get("product_snapshots", {})
    fees = data.get("channel_fees") or {}
    invoice = _invoice_of(db, doc["id"])
    shipment = db.execute("SELECT id FROM logistics_shipments WHERE id=?", (doc["id"],)).fetchone()
    items = []
    for index, item in enumerate(data.get("items", [])):
        snapshot = snapshots.get(item["sku"], {})
        category = (snapshot.get("category") or {}).get("path") or []
        items.append(
            {
                "id": numeric_id(db, "order_item", doc["id"] + ":" + str(index)),
                "codigo": item["sku"],
                "unidade": snapshot.get("unit", ""),
                "quantidade": float(item["quantity"]),
                "desconto": 0.0,
                "valor": float(item["unit_price"]),
                "descricao": snapshot.get("name", item["sku"]),
                "produto": {
                    "id": numeric_id(db, "product", item["sku"]),
                    "categoria": {
                        "id": numeric_id(db, "category", category[-1]["id"]),
                        "descricao": category[-1]["name"],
                    }
                    if category
                    else {},
                },
                "comissao": {"base": 0.0, "aliquota": 0.0, "valor": 0.0},
            }
        )
    return {
        **_order_base(erp, db, doc),
        "numeroPedidoCompra": data.get("purchase_order", ""),
        "desconto": {"valor": _float(data.get("discount")), "unidade": "REAL"},
        "observacoes": data.get("notes", ""),
        "observacoesInternas": data.get("internal_notes", ""),
        "outrasDespesas": _float(data.get("charges")),
        "itens": items,
        # A store sale is paid at once: one installment with the order total, as Bling keeps it.
        "parcelas": [
            {
                "id": numeric_id(db, "installment", doc["id"] + ":0"),
                "dataVencimento": data.get("date", ""),
                "valor": _float(data.get("total")),
                "observacoes": "",
                "formaPagamento": {
                    "id": numeric_id(db, "payment_method", data["payment_method_id"])
                    if data.get("payment_method_id")
                    else 0
                },
            }
        ]
        if data.get("total") is not None
        else [],
        "taxas": {
            "valorBase": _float(fees.get("base")),
            "custoFrete": _float(fees.get("shipping_cost")),
            "taxaComissao": _float(fees.get("commission")),
        },
        "transporte": {
            "frete": _float(data.get("freight")),
            "etiqueta": _delivery(data),
            "volumes": [{"id": numeric_id(db, "logistics_object", doc["id"])}] if shipment else [],
        },
        "notaFiscal": {"id": numeric_id(db, "invoice", invoice["id"]) if invoice else 0},
    }


def _orders(erp):
    return [o for o in erp.snapshot()["orders"] if not o["data"].get("discarded")]


def discard(erp, db, key, p):
    """Bling's trash can on an order: only a cancelled one with no account, note or shipment.
    It leaves the lists; the document and its history stay for the audit."""
    if not isinstance(p, dict) or set(p) != {"id", "reason"}:
        raise ERPError("order_discard", "Informe o pedido e o motivo.")
    doc, data = erp.document(db, p["id"], "order")
    if doc["status"] != "cancelled":
        raise ERPError("order_discard", "Só pedido cancelado pode ir para a lixeira.", 409)
    blockers = (
        ("SELECT 1 FROM accounts WHERE document_id=? AND status!='cancelled'", "conta"),
        ("SELECT 1 FROM logistics_shipments WHERE id=?", "envio"),
        ("SELECT 1 FROM fiscal_invoices WHERE order_id=?", "nota fiscal"),
    )
    for sql, label in blockers:
        try:
            found = db.execute(sql, (doc["id"],)).fetchone()
        except Exception:  # noqa: BLE001 - a module never used here has no table yet
            found = None
        if found:
            raise ERPError("order_discard", f"O pedido tem {label}; não vai para a lixeira.", 409)
    data["discarded"] = {"at": erp.now(), "reason": text(p["reason"], "motivo", 200)}
    erp.update_document(db, doc["id"], doc["status"], data)
    return {"id": doc["id"], "discarded": True, "external_writes": False}


def _command(erp, kind, payload):
    return erp.command(str(uuid4()), kind, payload, actor="integração davinci")


def _actor(db, key):
    row = (
        db.execute("SELECT actor FROM events WHERE operation_key=?", (key,)).fetchone()
        if key
        else None
    )
    return row["actor"] if row else "integração davinci"


def _occurrence(erp, db, doc_id, *, key, text, situation, occurred_at=None, actor=None):
    db.execute(
        "INSERT INTO order_occurrences(order_id,operation_key,occurred_at,text,situation_id,"
        "actor,created_at) VALUES (?,?,?,?,?,?,?)",
        (
            doc_id,
            key or str(uuid4()),
            occurred_at or erp.now(),
            text,
            situation,
            actor or _actor(db, key),
            erp.now(),
        ),
    )


def _set_situation(erp, db, doc, situation, *, key=None, note=None):
    """Bling keeps every situation change as an occurrence: who, when and to what."""
    if _current_situation(db, doc) != situation:
        text = (
            note
            if note is not None
            else ("Situação alterada" if key else "Situação alterada via API v3")
        )
        _occurrence(erp, db, doc["id"], key=key, text=text, situation=situation)
    db.execute(
        "INSERT INTO compat_order_situations VALUES (?,?,?,?) ON CONFLICT(order_id) DO UPDATE SET "
        "situation_id=excluded.situation_id,operation_key=excluded.operation_key,"
        "updated_at=excluded.updated_at",
        (doc["id"], situation, str(uuid4()), erp.now()),
    )


def apply_situation(erp, db, key, p):
    """The clock beside the status, as in Bling. Cancelling releases stock; the rest is a label."""
    if not isinstance(p, dict) or set(p) != {"id", "situation_id"}:
        raise ERPError("order_situation", "Informe o pedido e a situação.")
    situation = p["situation_id"]
    if type(situation) is not int or situation not in SITUATIONS:
        raise ERPError("order_situation", "A situação informada não existe.")
    doc, _ = erp.document(db, p["id"], "order")
    if situation == 12 and doc["status"] != "cancelled":
        if doc["status"] not in {"draft", "reserved"}:
            raise ERPError(
                "order_shipped", "Pedido já despachado: registre a devolução para desfazer.", 409
            )
        erp.apply(db, key, "order.cancel", {"id": doc["id"]})
    _set_situation(erp, db, doc, situation, key=key)
    return {"id": doc["id"], "situation_id": situation, "external_writes": False}


def apply_occurrence(erp, db, key, p):
    """Ocorrências, as in Bling: a dated note by a person, which may also change the situation."""
    import re

    if not isinstance(p, dict) or set(p) != {"id", "date", "time", "text", "situation_id"}:
        raise ERPError("order_occurrence", "Informe pedido, data, hora, ocorrência e situação.")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(p["date"])) or not re.fullmatch(
        r"\d{2}:\d{2}", str(p["time"])
    ):
        raise ERPError("order_occurrence", "Data ou hora inválida.")
    note = p["text"]
    if not isinstance(note, str) or len(note) > 500:
        raise ERPError("order_occurrence", "A ocorrência precisa ser texto de até 500 caracteres.")
    situation = p["situation_id"]
    if type(situation) is not int or situation not in SITUATIONS:
        raise ERPError("order_situation", "A situação informada não existe.")
    doc, _ = erp.document(db, p["id"], "order")
    from datetime import UTC, datetime

    try:
        moment = datetime.fromisoformat(f"{p['date']}T{p['time']}:00-03:00")
    except ValueError:
        raise ERPError("order_occurrence", "Data ou hora inválida.") from None
    when = moment.astimezone(UTC).isoformat()
    if _current_situation(db, doc) != situation:
        apply_situation(erp, db, key, {"id": doc["id"], "situation_id": situation})
        last = db.execute(
            "SELECT id FROM order_occurrences WHERE operation_key=? ORDER BY id DESC LIMIT 1",
            (key,),
        ).fetchone()
        db.execute(
            "UPDATE order_occurrences SET text=?,occurred_at=? WHERE id=?",
            (note.strip() or "Situação alterada", when, last["id"]),
        )
    else:
        if not note.strip():
            raise ERPError("order_occurrence", "Escreva a ocorrência ou escolha outra situação.")
        _occurrence(
            erp, db, doc["id"], key=key, text=note.strip(), situation=situation, occurred_at=when
        )
    return {"id": doc["id"], "situation_id": situation, "external_writes": False}


def remove_occurrence(erp, db, key, p):
    """The trash can in Ocorrências: hidden from the list, kept for the audit."""
    if not isinstance(p, dict) or set(p) != {"id", "occurrence_id"}:
        raise ERPError("order_occurrence", "Informe o pedido e a ocorrência.")
    row = db.execute(
        "SELECT id FROM order_occurrences WHERE id=? AND order_id=? AND removed_at IS NULL",
        (p["occurrence_id"], p["id"]),
    ).fetchone()
    if not row:
        raise ERPError("order_occurrence", "Ocorrência não encontrada.", 404)
    db.execute(
        "UPDATE order_occurrences SET removed_at=?,removed_by=? WHERE id=?",
        (erp.now(), _actor(db, key), row["id"]),
    )
    return {"id": p["id"], "occurrence_id": row["id"], "external_writes": False}


def occurrences(db, order_id):
    return [
        {
            "id": r["id"],
            "occurred_at": r["occurred_at"],
            "text": r["text"],
            "actor": r["actor"],
            "situation_id": r["situation_id"],
            "situation": SITUATIONS.get(r["situation_id"], ""),
        }
        for r in db.execute(
            "SELECT * FROM order_occurrences WHERE order_id=? AND removed_at IS NULL "
            "ORDER BY occurred_at DESC,id DESC",
            (order_id,),
        )
    ]


def own_number(number_id):
    return OWN_NUMBER_START + number_id - FIRST_OWN_ID if number_id > FIRST_OWN_ID else number_id


def situations_snapshot(db):
    return {
        "names": {str(k): v for k, v in SITUATIONS.items()},
        "orders": dict(db.execute("SELECT order_id,situation_id FROM compat_order_situations")),
        "numbers": {
            r[0]: own_number(r[1])
            for r in db.execute("SELECT local_id,numeric_id FROM compat_ids WHERE kind='order'")
        },
    }


def router(erp):
    api = APIRouter(prefix="/api/erp-local/bling/v3")

    def authorized(authorization):
        token = os.environ.get(TOKEN_ENV, "")
        supplied = (authorization or "").removeprefix("Bearer ").strip()
        return bool(token) and hmac.compare_digest(token.encode(), supplied.encode())

    async def gate(authorization: str | None = Header(default=None)):
        if not authorized(authorization):
            raise ERPError("compat_unauthorized", "Credencial da integração inválida.", 401)

    guard = [Depends(gate)]

    @api.get("/canais-venda", dependencies=guard)
    def channels(pagina: int = 1, limite: int = 100):
        with erp.connection() as db:
            rows = db.execute("SELECT channel,store,status,data FROM store_policies").fetchall()
        result = []
        for row in rows:
            data = json.loads(row["data"])
            profile = data.get("source_profile") or {}
            if profile.get("bling_store_id"):
                result.append(
                    {
                        "id": int(profile["bling_store_id"]),
                        "descricao": data.get("name") or profile.get("name") or row["store"],
                        "tipo": row["channel"],
                        "situacao": 1,
                    }
                )
        start = (pagina - 1) * limite
        return {"data": result[start : start + limite]}

    @api.get("/produtos", dependencies=guard)
    def products(request: Request, pagina: int = 1, limite: int = 100):
        limite = max(1, min(limite, 100))
        query = request.query_params
        code, search = (
            query.get("codigo"),
            (query.get("criterio") or query.get("nome") or "").lower(),
        )
        ids = {str(i) for i in query.getlist("idsProdutos[]")}
        codes = set(query.getlist("codigos[]"))
        rows = []
        with erp.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            for product in erp.snapshot()["products"]:
                if product["details"]["status"] == "deleted":
                    continue
                if code and product["sku"] != code:
                    continue
                if codes and product["sku"] not in codes:
                    continue
                if search and search not in product["name"].lower():
                    continue
                if ids and str(numeric_id(db, "product", product["sku"])) not in ids:
                    continue
                rows.append(product)
            start = (pagina - 1) * limite
            page = [_product_base(db, p) for p in rows[start : start + limite]]
            db.commit()
        return {"data": page}

    @api.get("/produtos/fornecedores", dependencies=guard)
    def product_suppliers_list(
        product_filter: int | None = Query(None, alias="idProduto"),
        supplier_filter: int | None = Query(None, alias="idFornecedor"),
    ):
        with erp.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            result = []
            for product in erp.snapshot()["products"]:
                pid = numeric_id(db, "product", product["sku"])
                if product_filter is not None and pid != product_filter:
                    continue
                for row in product["suppliers"]["rows"]:
                    contact = numeric_id(db, "contact", row["contact_id"])
                    if supplier_filter is not None and contact != supplier_filter:
                        continue
                    result.append(
                        {
                            "id": numeric_id(
                                db, "supplier_link", product["sku"] + ":" + row["contact_id"]
                            ),
                            "descricao": row["description"],
                            "codigo": row["code"],
                            "precoCusto": _float(row["cost_price"]),
                            "precoCompra": _float(row["purchase_price"]),
                            "padrao": row["is_default"],
                            "garantia": row["warranty_months"],
                            "produto": {"id": pid},
                            "fornecedor": {"id": contact},
                        }
                    )
            db.commit()
        return {"data": result}

    def _save_supplier(payload, *, link_id=None):
        with erp.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            product_ref = payload.get("idProduto") or (payload.get("produto") or {}).get("id")
            supplier_ref = payload.get("idContato") or (payload.get("fornecedor") or {}).get("id")
            sku = local_id(db, "product", product_ref)
            contact = local_id(db, "contact", supplier_ref)
            db.commit()
        if not sku or not contact:
            raise ERPError("compat_unknown", "Produto ou fornecedor não encontrado.", 404)
        product = next(p for p in erp.snapshot()["products"] if p["sku"] == sku)
        current = product["suppliers"]
        rows = [
            {
                k: r[k]
                for k in [
                    "contact_id",
                    "description",
                    "code",
                    "purchase_price",
                    "cost_price",
                    "warranty_months",
                    "is_default",
                ]
            }
            for r in current["rows"]
        ]
        existing = next((r for r in rows if r["contact_id"] == contact), None)
        if link_id is None and existing:
            return _error(400, "Registro duplicado.", field_code=279)
        row = existing or {
            "contact_id": contact,
            "description": "",
            "code": "",
            "purchase_price": None,
            "cost_price": None,
            "warranty_months": 0,
            "is_default": not rows,
        }
        row["cost_price"] = str(payload.get("precoCusto", row["cost_price"] or 0))
        if payload.get("precoCompra") is not None:
            row["purchase_price"] = str(payload["precoCompra"])
        if payload.get("codigo") is not None:
            row["code"] = payload["codigo"]
        if not existing:
            rows.append(row)
        _command(
            erp,
            "product.suppliers.save",
            {
                "sku": sku,
                "version": current["version"],
                "rows": rows,
                "reason": "Integração DaVinci",
            },
        )
        with erp.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            identifier = numeric_id(db, "supplier_link", sku + ":" + contact)
            db.commit()
        return {"data": {"id": identifier}}

    @api.post("/produtos/fornecedores", dependencies=guard, status_code=201)
    async def product_supplier_create(request: Request):
        return _save_supplier(await request.json())

    @api.put("/produtos/fornecedores/{link_id}", dependencies=guard)
    async def product_supplier_update(link_id: int, request: Request):
        return _save_supplier(await request.json(), link_id=link_id)

    @api.get("/produtos/{product_id}", dependencies=guard)
    def product_detail(product_id: int):
        with erp.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            sku = local_id(db, "product", product_id)
            db.commit()
            if not sku:
                return _error(404, "O produto não foi encontrado.", code="RESOURCE_NOT_FOUND")
            db.execute("BEGIN IMMEDIATE")
            product = next(p for p in erp.snapshot()["products"] if p["sku"] == sku)
            body = _product_detail(db, product)
            db.commit()
        return {"data": body}

    def _component_rows(db, structure):
        rows = []
        for part in structure.get("componentes", []):
            sku = local_id(db, "product", part["produto"]["id"])
            if not sku:
                raise ERPError("compat_unknown", "Componente não encontrado.", 404)
            rows.append({"sku": sku, "quantity": str(part["quantidade"])})
        return rows

    @api.post("/produtos", dependencies=guard, status_code=201)
    async def product_create(request: Request):
        body = await request.json()
        payload = {
            "sku": body.get("codigo"),
            "name": body.get("nome"),
            "kind": "service" if body.get("tipo") == "S" else "simple",
        }
        if body.get("preco") is not None:
            payload["price"] = str(body["preco"])
        if body.get("formato") == "E":
            with erp.connection() as db:
                db.execute("BEGIN IMMEDIATE")
                payload["components"] = _component_rows(db, body.get("estrutura") or {})
                db.commit()
            # Without parts it starts simple; the structure call completes the kit, as in Bling.
            if payload["components"] and body["estrutura"].get("tipoEstoque", "V") == "V":
                payload["kind"] = "kit"
            elif not payload["components"]:
                del payload["components"]
        result = _command(erp, "product.create", payload)
        category = (body.get("categoria") or {}).get("id")
        if category:
            with erp.connection() as db:
                category_id = local_id(db, "category", category)
            if category_id:
                _command(
                    erp,
                    "category.assign",
                    {"items": [{"sku": result["sku"], "version": 0, "category_id": category_id}]},
                )
        with erp.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            identifier = numeric_id(db, "product", result["sku"])
            db.commit()
        return {"data": {"id": identifier}}

    @api.patch("/produtos/{product_id}/precos", dependencies=guard)
    async def product_price(
        product_id: int,
        request: Request,
        store_filter: int | None = Query(None, alias="idLoja"),
    ):
        if store_filter is not None:
            return _error(
                422, "Preço por loja usa listas de preços no ERP próprio.", field_code="price_store"
            )
        body = await request.json()
        with erp.connection() as db:
            sku = local_id(db, "product", product_id)
            if not sku:
                return _error(404, "O produto não foi encontrado.", code="RESOURCE_NOT_FOUND")
            version = product_details.details(db, sku)["version"]
        _command(
            erp,
            "product.update",
            {"sku": sku, "version": version, "data": {"price": str(body["preco"])}},
        )
        return {"data": {"id": product_id}}

    @api.put("/produtos/estruturas/{product_id}", dependencies=guard)
    async def product_structure(product_id: int, request: Request):
        body = await request.json()
        with erp.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            sku = local_id(db, "product", product_id)
            if not sku:
                db.rollback()
                return _error(404, "O produto não foi encontrado.", code="RESOURCE_NOT_FOUND")
            components = _component_rows(db, body)
            version = product_structures.details(db, erp.product(db, sku))["version"]
            db.commit()
        _command(
            erp,
            "product.structure.replace",
            {
                "sku": sku,
                "version": version,
                "components": components,
                "virtual": body.get("tipoEstoque", "V") == "V",
                "reason": "Integração DaVinci",
            },
        )
        return {"data": {"id": product_id}}

    @api.get("/categorias/produtos", dependencies=guard)
    def category_list(pagina: int = 1, limite: int = 100):
        with erp.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = categories.snapshot(db)
            start = (pagina - 1) * limite
            page = [
                {
                    "id": numeric_id(db, "category", c["id"]),
                    "descricao": c["name"],
                    "categoriaPai": {
                        "id": numeric_id(db, "category", c["parent_id"]) if c["parent_id"] else 0
                    },
                }
                for c in rows[start : start + limite]
            ]
            db.commit()
        return {"data": page}

    @api.post("/categorias/produtos", dependencies=guard, status_code=201)
    async def category_create(request: Request):
        body = await request.json()
        result = _command(erp, "category.save", {"data": {"name": body.get("descricao")}})
        with erp.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            identifier = numeric_id(db, "category", result["id"])
            db.commit()
        return {"data": {"id": identifier}}

    @api.get("/depositos", dependencies=guard)
    def depots(pagina: int = 1, limite: int = 100):
        with erp.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = erp.snapshot()["deposits"]
            start = (pagina - 1) * limite
            page = [
                {
                    "id": numeric_id(db, "depot", d["id"]),
                    "descricao": d["name"],
                    "situacao": 1 if d["status"] == "active" else 0,
                    "padrao": bool(d["is_default"]),
                    "desconsiderarSaldo": bool(d["exclude_from_total"]),
                }
                for d in rows[start : start + limite]
            ]
            db.commit()
        return {"data": page}

    @api.post("/estoques", dependencies=guard, status_code=201)
    async def stock_post(request: Request):
        body = await request.json()
        with erp.connection() as db:
            sku = local_id(db, "product", body["produto"]["id"])
            depot = local_id(db, "depot", (body.get("deposito") or {}).get("id", -1))
        if not sku or not depot:
            return _error(404, "Produto ou depósito não encontrado.", code="RESOURCE_NOT_FOUND")
        operation = body.get("operacao")
        if operation not in {"B", "E", "S"}:
            return _error(400, "Operação de estoque inválida.", field_code="stock_operation")
        payload = {
            "sku": sku,
            "depot": depot,
            "type": operation,
            "quantity": str(body["quantidade"]),
            "reason": body.get("observacoes") or "Integração DaVinci",
        }
        if body.get("custo"):
            payload["unit_cost"] = str(body["custo"])
        _command(erp, "stock.post", payload)
        return {"data": {"id": int(uuid4().int >> 80)}}

    @api.get("/contatos", dependencies=guard)
    def contacts_list(pesquisa: str = "", pagina: int = 1, limite: int = 100):
        with erp.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            term = pesquisa.lower()
            rows = [c for c in contacts.snapshot(db) if term in c["data"]["name"].lower()]
            start = (pagina - 1) * limite
            page = [
                {
                    "id": numeric_id(db, "contact", c["id"]),
                    "nome": c["data"]["name"],
                    "codigo": str(c["code"]),
                    "situacao": "A" if c["data"].get("status", "active") == "active" else "I",
                    "numeroDocumento": c["data"].get("document", ""),
                    "telefone": c["data"].get("phone", ""),
                    "celular": c["data"].get("mobile", ""),
                }
                for c in rows[start : start + limite]
            ]
            db.commit()
        return {"data": page}

    @api.get("/contatos/{contact_id}", dependencies=guard)
    def contact_detail(contact_id: int):
        with erp.connection() as db:
            identifier = local_id(db, "contact", contact_id)
            contact = next((c for c in contacts.snapshot(db) if c["id"] == identifier), None)
        if not contact:
            return _error(404, "O contato não foi encontrado.", code="RESOURCE_NOT_FOUND")
        d = contact["data"]
        return {
            "data": {
                "id": contact_id,
                "nome": d["name"],
                "codigo": str(contact["code"]),
                "situacao": "A" if d.get("status", "active") == "active" else "I",
                "numeroDocumento": d.get("document", ""),
                "telefone": d.get("phone", ""),
                "celular": d.get("mobile", ""),
                "email": d.get("email", ""),
                "endereco": {
                    "geral": {
                        "endereco": d.get("address", ""),
                        "cep": d.get("zip", ""),
                        "bairro": d.get("district", ""),
                        "municipio": d.get("city", ""),
                        "uf": d.get("state", ""),
                        "numero": d.get("number", ""),
                        "complemento": d.get("complement", ""),
                    }
                },
            }
        }

    @api.get("/situacoes/modulos", dependencies=guard)
    def situation_modules():
        return {"data": [{"id": MODULE_SALES, "nome": "Vendas", "criarSituacoes": False}]}

    @api.get("/situacoes/modulos/{module_id}", dependencies=guard)
    def situation_list(module_id: int):
        if module_id != MODULE_SALES:
            return {"data": []}
        return {
            "data": [
                {"id": identifier, "nome": name, "idHerdado": 0, "cor": ""}
                for identifier, name in SITUATIONS.items()
            ]
        }

    @api.get("/pedidos/vendas", dependencies=guard)
    def order_list(request: Request, pagina: int = 1, limite: int = 100):
        query = request.query_params
        limite = max(1, min(limite, 100))
        numbers = set(query.getlist("numerosLojas[]"))
        situation = query.get("idSituacao")
        store = query.get("idLoja")
        start_day, end_day = query.get("dataInicial"), query.get("dataFinal")
        with erp.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = []
            for doc in _orders(erp):
                data = doc["data"]
                day = data.get("date") or str(data.get("created_at", ""))[:10]
                if numbers and str(data.get("external_id")) not in numbers:
                    continue
                if start_day and day < start_day or end_day and day > end_day:
                    continue
                if situation and _current_situation(db, doc) != int(situation):
                    continue
                if store and _store_id(erp, db, data) != int(store):
                    continue
                rows.append(doc)
            start = (pagina - 1) * limite
            page = [_order_base(erp, db, d) for d in rows[start : start + limite]]
            db.commit()
        return {"data": page}

    def _find_order(db, order_id):
        identifier = local_id(db, "order", order_id)
        return next((d for d in _orders(erp) if d["id"] == identifier), None)

    @api.get("/pedidos/vendas/{order_id}", dependencies=guard)
    def order_detail(order_id: int):
        with erp.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            doc = _find_order(db, order_id)
            if not doc:
                db.rollback()
                return _error(404, "O pedido não foi encontrado.", code="RESOURCE_NOT_FOUND")
            body = _order_detail(erp, db, doc)
            db.commit()
        return {"data": body}

    @api.patch("/pedidos/vendas/{order_id}/situacoes/{situation_id}", dependencies=guard)
    def order_situation(order_id: int, situation_id: int):
        if situation_id not in SITUATIONS:
            return _error(400, "A situação informada não existe.", field_code="situation_unknown")
        with erp.connection() as db:
            doc = _find_order(db, order_id)
            if not doc:
                return _error(404, "O pedido não foi encontrado.", code="RESOURCE_NOT_FOUND")
            current = _current_situation(db, doc)
        if current == situation_id:
            return _error(400, "A venda possui a mesma situação.", field_code=50)
        if situation_id == 12:
            if doc["status"] not in {"draft", "reserved"}:
                return _error(
                    409,
                    "Pedido já despachado: registre a devolução para desfazer o estoque.",
                    field_code="order_shipped",
                )
            _command(erp, "order.cancel", {"id": doc["id"]})
        with erp.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            _set_situation(erp, db, doc, situation_id)
            db.commit()
        return Response(status_code=204)

    @api.put("/pedidos/vendas/{order_id}", dependencies=guard)
    async def order_update(order_id: int, request: Request):
        body = await request.json()
        with erp.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            doc = _find_order(db, order_id)
            if not doc:
                db.rollback()
                return _error(404, "O pedido não foi encontrado.", code="RESOURCE_NOT_FOUND")
            current = _order_detail(erp, db, doc)
            db.commit()
        sent = {
            (i.get("codigo"), float(i.get("quantidade", 0)), float(i.get("valor", 0)))
            for i in body.get("itens", current["itens"])
        }
        held = {(i["codigo"], i["quantidade"], i["valor"]) for i in current["itens"]}
        if sent != held:
            return _error(
                422,
                "Alterar itens exige a edição do pedido no ERP próprio.",
                field_code="order_items",
            )
        _command(
            erp,
            "order.notes",
            {
                "id": doc["id"],
                "notes": body.get("observacoes", current["observacoes"]),
                "internal_notes": body.get("observacoesInternas", current["observacoesInternas"]),
            },
        )
        return {"data": {"id": order_id}}

    @api.post("/contas/pagar", dependencies=guard, status_code=201)
    async def payable_create(request: Request):
        body = await request.json()
        with erp.connection() as db:
            contact = local_id(db, "contact", (body.get("contato") or {}).get("id", -1))
            party = next(
                (c["data"]["name"] for c in contacts.snapshot(db) if c["id"] == contact), None
            )
        if not party:
            return _error(404, "O contato não foi encontrado.", code="RESOURCE_NOT_FOUND")
        result = _command(
            erp,
            "finance.entry.create",
            {
                "kind": "payable",
                "reference": body.get("numeroDocumento") or "davinci-" + str(uuid4()),
                "party": party,
                "amount": str(body["valor"]),
                "due_date": body["vencimento"],
                "issued_on": body.get("dataEmissao") or body["vencimento"],
                "competence_on": body.get("competencia") or body["vencimento"],
                "history": body.get("historico") or "",
                "document_number": body.get("numeroDocumento") or "",
            },
        )
        with erp.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            identifier = numeric_id(db, "payable", str(result.get("id") or result))
            db.commit()
        return {"data": {"id": identifier}}

    @api.get("/logisticas/objetos/{object_id}", dependencies=guard)
    def logistics_object(object_id: int):
        with erp.connection() as db:
            identifier = local_id(db, "logistics_object", object_id)
            row = db.execute(
                "SELECT * FROM logistics_shipments WHERE id=?", (identifier,)
            ).fetchone()
            if not row:
                return _error(404, "O objeto não foi encontrado.", code="RESOURCE_NOT_FOUND")
            data = json.loads(row["data"])
            track = db.execute(
                "SELECT * FROM logistics_tracking WHERE shipment_id=? ORDER BY seq DESC",
                (identifier,),
            ).fetchone()
        return {
            "data": {
                "id": object_id,
                "servico": {"nome": data.get("service", "")},
                "dataSaida": data.get("dispatched_at", "0000-00-00")[:10],
                "prazoEntregaPrevisto": data.get("delivery_days", 0),
                "rastreamento": {
                    "codigo": data.get("tracking_code", ""),
                    "descricao": track["status"] if track else "",
                    "ultimaAlteracao": track["occurred_at"] if track else "",
                },
            }
        }

    @api.get("/estoques/saldos", dependencies=guard)
    def stock_balances(request: Request):
        wanted = {str(i) for i in request.query_params.getlist("idsProdutos[]")}
        depot_filter = request.query_params.get("idDeposito")
        result = []
        with erp.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            for product in erp.snapshot()["products"]:
                identifier = numeric_id(db, "product", product["sku"])
                if wanted and str(identifier) not in wanted:
                    continue
                active = product["details"]["status"] == "active"
                depots = [
                    {
                        "id": numeric_id(db, "depot", b["depot"]),
                        "saldoFisico": _float(b["physical"]),
                        "saldoVirtual": _float(b["available"]) if active else 0.0,
                    }
                    for b in product["balances"]
                ]
                if depot_filter:
                    depots = [d for d in depots if str(d["id"]) == depot_filter]
                counted = [b for b in product["balances"] if b.get("in_total") is not False]
                result.append(
                    {
                        "produto": {"id": identifier},
                        "saldoFisicoTotal": sum(_float(b["physical"]) for b in counted),
                        "saldoVirtualTotal": _available(product),
                        "depositos": depots,
                    }
                )
            db.commit()
        return {"data": result}

    def _invoice_view(db, row):
        data = json.loads(row["data"])
        order = db.execute("SELECT data FROM documents WHERE id=?", (row["order_id"],)).fetchone()
        contact = json.loads(order[0]).get("contact") or {} if order else {}
        situation = {"draft": 1, "sending": 2, "rejected": 4, "authorized": 5, "denied": 3}
        return {
            "id": numeric_id(db, "invoice", row["id"]),
            "tipo": 1,
            "situacao": situation.get(row["status"], 1),
            "numero": str(row["number"]),
            "serie": row["series"],
            "dataEmissao": data.get("issued_at", row["updated_at"][:19].replace("T", " ")),
            "dataOperacao": data.get("issued_at", row["updated_at"][:19].replace("T", " ")),
            "chaveAcesso": row["access_key"],
            "valorNota": _float(data.get("total")),
            "contato": {
                "nome": data.get("recipient") or contact.get("name", ""),
                "numeroDocumento": contact.get("document", ""),
            },
        }

    @api.get("/nfe", dependencies=guard)
    def invoice_list(request: Request, pagina: int = 1, limite: int = 100):
        query = request.query_params
        start_day = (query.get("dataEmissaoInicial") or "")[:10]
        end_day = (query.get("dataEmissaoFinal") or "")[:10]
        limite = max(1, min(limite, 100))
        with erp.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = [
                row
                for row in db.execute("SELECT * FROM fiscal_invoices ORDER BY rowid DESC")
                if (not start_day or row["updated_at"][:10] >= start_day)
                and (not end_day or row["updated_at"][:10] <= end_day)
            ]
            start = (pagina - 1) * limite
            page = [_invoice_view(db, r) for r in rows[start : start + limite]]
            db.commit()
        return {"data": page}

    @api.get("/nfe/{invoice_id}", dependencies=guard)
    def invoice_detail(invoice_id: int):
        with erp.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            identifier = local_id(db, "invoice", invoice_id)
            row = db.execute("SELECT * FROM fiscal_invoices WHERE id=?", (identifier,)).fetchone()
            if not row:
                db.rollback()
                return _error(404, "A nota não foi encontrada.", code="RESOURCE_NOT_FOUND")
            body = _invoice_view(db, row)
            db.commit()
        return {"data": {**body, "xml": row["authorized_xml"] or "", "linkPDF": ""}}

    return api


def notes_command(erp, db, p):
    if not isinstance(p, dict) or set(p) != {"id", "notes", "internal_notes"}:
        raise ERPError("order_fields", "Informe pedido e observações.")
    doc, data = erp.document(db, p["id"], "order")
    for key in ["notes", "internal_notes"]:
        if not isinstance(p[key], str) or len(p[key]) > 5000:
            raise ERPError("order_notes", "Observações precisam ser texto de até 5000 caracteres.")
        data[key] = p[key]
    erp.update_document(db, doc["id"], doc["status"], data)
    return {"id": doc["id"], "external_writes": False}


ORDER_EVENTS = {
    "order.import",
    "order.save",
    "order.confirm",
    "order.situation",
    "order.compat_situation",
    "order.occurrence",
    "order.cancel",
    "order.ship",
    "order.return",
    "order.notes",
    "order.stock",
    "logistics.dispatch",
}


def capture_events(erp, db, kind, payload, result):
    """Queue the same notification Bling would send. Delivery is a separate, opt-in step."""
    identifier = (result or {}).get("id") or (payload or {}).get("id")
    if kind not in ORDER_EVENTS or not identifier:
        return
    if not db.execute(
        "SELECT 1 FROM documents WHERE id=? AND kind='order'", (identifier,)
    ).fetchone():
        return
    body = {"event": "pedido.alteracao", "dados": {"id": numeric_id(db, "order", identifier)}}
    db.execute(
        "INSERT INTO compat_outbox(event,body,created_at) VALUES (?,?,?)",
        ("pedido.alteracao", json.dumps(body, sort_keys=True), erp.now()),
    )


def _free_stock(db, skus):
    from app.erp_local.deposits import snapshot as depot_snapshot

    counted = {d["id"] for d in depot_snapshot(db) if not d["exclude_from_total"]}
    free = defaultdict(Decimal)
    marks = ",".join("?" * len(skus))
    for row in db.execute(
        f"SELECT sku,depot,physical,reserved FROM balances WHERE sku IN ({marks})",  # noqa: S608
        tuple(skus),
    ):
        if row["depot"] in counted:
            free[row["sku"]] += Decimal(row["physical"]) - Decimal(row["reserved"])
    return free


def capture_stock_events(erp, db, operation_key):
    """Tell DaVinci the same stock news Bling would, including every kit using a moved part."""
    moved = {
        r[0]
        for r in db.execute(
            "SELECT DISTINCT sku FROM movements WHERE operation_key=?", (operation_key,)
        )
    }
    if not moved:
        return
    affected = set(moved)
    for row in db.execute("SELECT sku,components FROM products WHERE kind='kit'"):
        if any(p["sku"] in moved for p in json.loads(row["components"])):
            affected.add(row["sku"])
    products = {
        row["sku"]: row
        for row in db.execute("SELECT sku,kind,components FROM products")
        if row["sku"] in affected
    }
    parts_of = {
        sku: json.loads(row["components"]) for sku, row in products.items() if row["kind"] == "kit"
    }
    needed = (affected | {p["sku"] for parts in parts_of.values() for p in parts}) - {""}
    free = _free_stock(db, sorted(needed))
    for sku in sorted(affected):
        product = products.get(sku)
        if not product or product["kind"] == "service":
            continue
        value = (
            min(
                (free[p["sku"]] // Decimal(p["quantity"]) for p in parts_of[sku]),
                default=Decimal(0),
            )
            if sku in parts_of
            else free[sku]
        )
        body = {
            "event": "estoque.alteracao",
            "dados": {
                "id": numeric_id(db, "product", sku),
                "codigo": sku,
                "estoque": {"saldoVirtualTotal": float(value)},
            },
        }
        db.execute(
            "INSERT INTO compat_outbox(event,body,created_at) VALUES (?,?,?)",
            ("estoque.alteracao", json.dumps(body, sort_keys=True), erp.now()),
        )


def deliver_outbox(erp, send, secret, *, limit=100, now=None):
    """`send(body, headers)` returns True when the receiver accepted the event."""
    moment = now or erp.now()
    with erp.connection() as db:
        rows = db.execute(
            "SELECT * FROM compat_outbox WHERE delivered_at IS NULL "
            "AND (next_attempt_at IS NULL OR next_attempt_at<=?) ORDER BY id LIMIT ?",
            (moment, limit),
        ).fetchall()
    delivered = 0
    for row in rows:
        raw = row["body"].encode()
        signature = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        headers = {
            "Content-Type": "application/json",
            "X-Bling-Signature": "sha256=" + signature,
            "X-Bling-Event": row["event"],
            "X-Bling-Delivery": "erp-" + str(row["id"]),
        }
        try:
            accepted = send(raw, headers)
        except OSError:
            accepted = False
        with erp.connection() as db:
            if accepted:
                db.execute(
                    "UPDATE compat_outbox SET delivered_at=?,attempts=attempts+1 WHERE id=?",
                    (moment, row["id"]),
                )
                delivered += 1
            else:
                wait = min(60 * 2 ** row["attempts"], 3600)
                retry = datetime.fromisoformat(moment) + timedelta(seconds=wait)
                db.execute(
                    "UPDATE compat_outbox SET attempts=attempts+1,next_attempt_at=? WHERE id=?",
                    (retry.isoformat(), row["id"]),
                )
    return {"delivered": delivered, "pending": len(rows) - delivered}
