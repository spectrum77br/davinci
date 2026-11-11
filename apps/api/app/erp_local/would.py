"""O que o ERP faria: na homologação, cada ação que sairia para fora responde o que mandaria.

Só vale onde ERP_HOMOLOG=1. A etiqueta vira uma página "sem valor" com o envio que seria baixado da
loja; a NF-e mostra o que seria assinado e enviado à SEFAZ. Nada é pedido à loja nem à SEFAZ, e
tudo fica registrado no dia, para comparar com o que a equipe fez no Bling.
"""

import json
import os
import re

from app.erp_local import native_ingest, recipient_capture
from app.erp_local.core import ERPError

SCHEMA = """
CREATE TABLE IF NOT EXISTS homolog_would (
 id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, action TEXT NOT NULL,
 target TEXT NOT NULL, detail TEXT NOT NULL);
"""
CHANNELS = {"ml": "Mercado Livre", "shopee": "Shopee", "tiktok": "TikTok Shop"}
ACTIONS = {"label": "Etiqueta de envio", "invoice": "Envio da NF-e à SEFAZ"}


def active():
    return os.environ.get("ERP_HOMOLOG") == "1"


def record(erp, action, target, detail):
    with erp.connection() as db:
        db.executescript(SCHEMA)
        db.execute(
            "INSERT INTO homolog_would(at,action,target,detail) VALUES (?,?,?,?)",
            (erp.now(), action, target, json.dumps(detail, ensure_ascii=False)),
        )


def listing(db, limit=200):
    try:
        rows = db.execute(
            "SELECT at,action,target,detail FROM homolog_would ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    except Exception:  # noqa: BLE001 - the table appears with the first action
        return []
    return [
        {
            "at": r["at"],
            "action": r["action"],
            "name": ACTIONS.get(r["action"], r["action"]),
            "target": r["target"],
            "detail": json.loads(r["detail"]),
        }
        for r in rows
    ]


def _store_name(db, channel, store):
    row = db.execute(
        "SELECT data FROM store_policies WHERE channel=? AND store=?", (channel, store)
    ).fetchone()
    return (json.loads(row[0]).get("name") if row else None) or store


def _place(buyer):
    address = buyer.get("address") if isinstance(buyer.get("address"), dict) else {}
    city = buyer.get("city") or address.get("city") or ""
    uf = buyer.get("uf") or buyer.get("state") or address.get("uf") or address.get("state") or ""
    zip_code = buyer.get("zip") or buyer.get("postal_code") or address.get("zip") or ""
    return " · ".join(p for p in (f"{city}/{uf}".strip("/"), str(zip_code)) if p)


def label_plan(db, channel, store, number):
    """What printing this sale's label would do, as one page can tell."""
    plan = {
        "channel": CHANNELS.get(channel, channel),
        "store": _store_name(db, channel, store),
        "number": number,
    }
    from app.erp_local import store_labels

    unit = native_ingest._units(db, channel, store).get(number) or {}
    live = [o for o in unit.values() if not native_ingest._is_cancelled(o)]
    allowed = native_ingest.OPEN_STATUS.get(channel) or set()
    statuses = sorted({str(o.get("status") or "").upper() for o in live})
    if unit and not live:
        plan["would"] = "Não pediria etiqueta: a venda foi cancelada na loja."
    elif live and any(s not in allowed for s in statuses):
        plan["would"] = (
            "Não pediria etiqueta: a loja ainda não liberou o envio "
            f"(situação na loja: {', '.join(statuses)})."
        )
    else:
        try:
            reference = store_labels.reference(db, channel, store, number)
            plan["would"] = (
                f"Baixaria da {plan['channel']} a etiqueta do envio {reference} "
                "e a imprimiria aqui."
            )
            plan["reference"] = str(reference)
        except ERPError as error:
            plan["would"] = f"Não pediria etiqueta: {error.message}"
    buyer = recipient_capture.for_order(db, channel, store, number) or {}
    plan["buyer"] = buyer.get("name") or "comprador ainda não lido"
    plan["place"] = _place(buyer)
    plan["items"] = [
        f"{native_ingest._quantity(channel, item) or '?'} × "
        f"{item.get('seller_sku') or item.get('sku') or 'sem SKU'}"
        for order in unit.values()
        for item in order.get("items") or []
    ]
    return plan


def label_pdf(erp, keys):
    """One page per sale, in the order asked, each saying what would have been printed."""
    import fitz

    document = fitz.open()
    with erp.connection() as db:
        plans = [label_plan(db, *key) for key in keys]
    for plan in plans:
        page = document.new_page(width=283, height=425)
        lines = [
            "HOMOLOGAÇÃO - SEM VALOR",
            "Não use para enviar.",
            "",
            f"{plan['channel']} · {plan['store']}",
            f"Venda {plan['number']}",
            "",
            plan["would"],
            "",
            f"Para: {plan['buyer']}",
            plan["place"],
            "",
            *plan["items"][:12],
        ]
        page.insert_textbox(fitz.Rect(18, 18, 265, 407), "\n".join(lines), fontsize=10)
        record(erp, "label", f"{plan['channel']} {plan['number']}", plan)
    return document.tobytes()


def invoice_plan(db, identifier):
    row = db.execute(
        "SELECT i.*,c.data AS company FROM fiscal_invoices i "
        "JOIN fiscal_companies c ON c.id=i.company_id WHERE i.id=?",
        (identifier,),
    ).fetchone()
    if not row:
        raise ERPError("fiscal_invoice", "Nota não encontrada.", 404)
    company, data = json.loads(row["company"]), json.loads(row["data"])
    uf = (company.get("address") or {}).get("uf") or "?"
    total = data.get("total") or (data.get("totals") or {}).get("invoice") or ""
    recipient = data.get("recipient") or ""
    if isinstance(recipient, dict):
        recipient = recipient.get("name") or ""
    xml = row["xml"] or ""
    cfops = sorted(set(re.findall(r"<CFOP>(\d{4})</CFOP>", xml)))
    place = re.search(r"<dest>.*?<UF>([A-Z]{2})</UF>", xml, re.S)
    if row["status"] == "authorized":
        would = "Nada: esta nota já está autorizada."
    elif row["status"] not in {"draft", "sending"}:
        would = "Nada: prepare a nota novamente antes de enviar."
    else:
        would = (
            f"Assinaria com o certificado de {company.get('name') or company.get('cnpj')} e "
            f"enviaria a nota {row['number']} (série {row['series']}) de R$ {total}"
            f"{', CFOP ' + '/'.join(cfops) if cfops else ''}"
            f"{', para ' + place.group(1) if place else ''}, à SEFAZ de {uf} em produção. "
            "Depois guardaria o protocolo e mandaria a nota à loja."
        )
    if not company.get("technical") and row["status"] in {"draft", "sending"}:
        would += " Antes, faltaria informar o responsável técnico da emissão (decisão pendente)."
    return {
        "number": row["number"],
        "series": row["series"],
        "access_key": row["access_key"],
        "company": company.get("name") or company.get("cnpj"),
        "recipient": recipient,
        "total": str(total),
        "cfops": cfops,
        "status": row["status"],
        "would": would,
    }


def transmit(erp, identifier):
    with erp.connection() as db:
        plan = invoice_plan(db, identifier)
    record(erp, "invoice", f"NF {plan['number']}/{plan['series']}", plan)
    return {"id": identifier, "status": plan["status"], "would": True, "message": plan["would"]}


PLANS = """
CREATE TABLE IF NOT EXISTS homolog_plans (
 kind TEXT PRIMARY KEY, at TEXT NOT NULL, rows TEXT NOT NULL);
"""
ACTIONS.update({"stock": "Estoque nos anúncios", "price": "Preço nos anúncios"})


def _listings(db):
    """Listings as DaVinci knows them, from the reference copy this base follows."""
    refs = {
        kind: [
            json.loads(r[0])
            for r in db.execute("SELECT data FROM reference_rows WHERE kind=?", (kind,))
        ]
        for kind in ("links", "products", "integrations")
    }
    products = {str(p.get("id")): p for p in refs["products"]}
    stores = {str(i.get("id")): i for i in refs["integrations"]}
    links = []
    for link in refs["links"]:
        if link.get("platform") == "bling":
            continue
        product = products.get(str(link.get("product_id"))) or {}
        store = stores.get(str(link.get("integration_id"))) or {}
        links.append(
            {
                "channel": link.get("platform") or "",
                "store": store.get("name") or "",
                "listing": link.get("external_id") or "",
                "variation": link.get("variation_id") or "",
                "listing_stock": link.get("stock"),
                "listing_price": link.get("price"),
                "sku": product.get("sku") or "",
                "product_stock": product.get("stock"),
                "situation": product.get("situacao") or "A",
                "bling_id": product.get("bling_product_id"),
                # The copy does not carry DaVinci's dead-listing mark: those count as listings.
                "dead": False,
                "archived": bool(store.get("archived_at")),
                "vacation": bool(store.get("vacation_mode")),
            }
        )
    davinci_products = [
        {"sku": str(p["sku"]).lower(), "stock": p.get("stock")}
        for p in refs["products"]
        if p.get("sku") and (p.get("situacao") or "A") == "A"
    ]
    return links, davinci_products


def _keep(erp, kind, rows):
    with erp.connection() as db:
        db.executescript(PLANS)
        db.execute(
            "INSERT OR REPLACE INTO homolog_plans VALUES (?,?,?)",
            (kind, erp.now(), json.dumps(rows, ensure_ascii=False)),
        )


def plan_rows(db, kind):
    try:
        row = db.execute("SELECT rows FROM homolog_plans WHERE kind=?", (kind,)).fetchone()
    except Exception:  # noqa: BLE001 - the table appears with the first plan
        return []
    return json.loads(row[0]) if row else []


def stock_sync(erp):
    """What "Sincronizar estoque na loja virtual" would send, listing by listing."""
    from app.erp_local import stock_publish_preview as preview

    with erp.connection() as db:
        links, products = _listings(db)
        result = preview.compare(db, links, products, preview.family_settings())
    _keep(erp, "stock", result["diferencas"])
    reasons = [
        f"{name}: {count}"
        for name, count in result["motivos"].items()
        if name != preview.REASONS["igual"]
    ][:3]
    text = (
        f"Mandaria o estoque do ERP a {result['anuncios']} anúncios: {result['iguais']} com o "
        f"mesmo número que o DaVinci manda hoje e {result['diferentes']} com número diferente"
        + (f" ({'; '.join(reasons)})" if reasons else "")
        + ". Nada foi enviado às lojas."
    )
    detail = {
        k: result[k] for k in ("anuncios", "iguais", "diferentes", "motivos", "fora_da_conta")
    }
    record(erp, "stock", "Todos os anúncios", {**detail, "would": text})
    return {"would": True, "message": text, "csv": "/api/erp-local/homolog/plans/stock.csv"}


def _money(value):
    from decimal import Decimal, InvalidOperation

    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number.quantize(Decimal("0.01")) if number.is_finite() else None


def price_sync(erp):
    """What "Sincronizar preços na loja virtual" would send: the ERP's own price for each listing.

    The ERP has no store price list yet, so its price is the product's registered price. Today the
    listings carry DaVinci's pricing, so every difference is a price this button would overwrite.
    """
    rows, same, missing, considered = [], 0, 0, 0
    with erp.connection() as db:
        links, _ = _listings(db)
        by_bling = {}
        for r in db.execute("SELECT sku,data FROM catalog_origin"):
            source = json.loads(r["data"]).get("bling_product_id")
            if source:
                by_bling[str(source)] = r["sku"]
        prices = {
            r[0].lower(): (r[0], r[1]) for r in db.execute("SELECT sku,price FROM product_pricing")
        }
        for link in links:
            if link["archived"] or link["vacation"]:
                continue
            considered += 1
            sku = (
                by_bling.get(str(link["bling_id"])) or (prices.get(link["sku"].lower()) or ("",))[0]
            )
            own = _money((prices.get(sku.lower()) or (None, None))[1]) if sku else None
            current = _money(link["listing_price"])
            if own is None:
                missing += 1
                continue
            if current == own:
                same += 1
                continue
            rows.append(
                {
                    "canal": link["channel"],
                    "loja": link["store"],
                    "anuncio": link["listing"],
                    "variacao": link["variation"],
                    "sku": sku,
                    "preco_no_anuncio": str(current) if current is not None else "",
                    "erp_mandaria": str(own),
                }
            )
    _keep(erp, "price", rows)
    text = (
        f"Mandaria o preço de cadastro do ERP a {considered - missing} anúncios: {same} já estão "
        f"com esse preço e {len(rows)} mudariam de preço. Hoje o preço dos anúncios vem da "
        "precificação do DaVinci, então ligar isto sobrescreveria esses preços. "
        f"Sem preço no ERP: {missing}. Nada foi enviado às lojas."
    )
    record(
        erp,
        "price",
        "Todos os anúncios",
        {
            "anuncios": considered,
            "iguais": same,
            "diferentes": len(rows),
            "sem_preco": missing,
            "would": text,
        },
    )
    return {"would": True, "message": text, "csv": "/api/erp-local/homolog/plans/price.csv"}


def plan_csv(db, kind):
    import csv
    import io

    rows = plan_rows(db, kind)
    stream = io.StringIO()
    if rows:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), delimiter=";")
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    k: ("'" + str(v)) if str(v)[:1] in ("=", "+", "-", "@") else v
                    for k, v in row.items()
                }
            )
    return stream.getvalue()
