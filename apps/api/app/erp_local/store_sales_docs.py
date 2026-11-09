"""One store sale in detail, and the papers made from the selected ones.

Everything here only reads what was already read from the stores: nothing goes back to them,
nothing reserves stock and nothing is fiscal. The papers follow what Bling offers under
"Mais ações": spreadsheet, addresses, production report and content declaration.
"""

import csv
import io
import json
from decimal import Decimal, InvalidOperation
from html import escape

from app.erp_local import native_ingest, recipient_capture, store_sales
from app.erp_local.core import ERPError

MAX_PICKS = 200


def _amount(value):
    if isinstance(value, dict):
        value = value.get("amount")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number.is_finite() else None


def _money(value):
    return "" if value is None else f"{value:.2f}".replace(".", ",")


def _qty(value):
    return "" if value is None else f"{value.normalize():f}".replace(".", ",")


def picks(items):
    if not isinstance(items, list) or not items:
        raise ERPError("picks_invalid", "Selecione ao menos uma venda.", 422)
    if len(items) > MAX_PICKS:
        raise ERPError("picks_invalid", f"Selecione no máximo {MAX_PICKS} vendas.", 422)
    chosen = []
    for item in items:
        if not isinstance(item, dict):
            raise ERPError("picks_invalid", "Venda selecionada inválida.", 422)
        fields = [str(item.get(k) or "").strip() for k in ("channel", "store", "number")]
        if not all(fields) or any(len(f) > 120 for f in fields):
            raise ERPError("picks_invalid", "Venda selecionada inválida.", 422)
        chosen.append(tuple(fields))
    return chosen


def detail(db, channel, store, number):
    unit = native_ingest._units(db, channel, store).get(str(number))
    if not unit:
        raise ERPError("store_sale_not_found", "Venda da loja não encontrada.", 404)
    orders = list(unit.values())
    names = store_sales._names(db)
    captured = None
    for external in unit:
        reading = recipient_capture.for_order(db, channel, store, external)
        if reading and (captured is None or reading["ready"]):
            captured = reading
    items = []
    for order in orders:
        for raw in order.get("items") or []:
            resolved = native_ingest._resolve(db, store, raw, channel)
            sku = resolved.get("sku") or ""
            row = (
                db.execute("SELECT name FROM products WHERE sku=?", (sku,)).fetchone()
                if sku
                else None
            )
            quantity = _amount(native_ingest._quantity(channel, raw))
            price = _amount(native_ingest._price(raw))
            items.append(
                {
                    "sku": sku,
                    "seller_sku": raw.get("seller_sku") or "",
                    "name": row["name"] if row else "",
                    "quantity": quantity,
                    "unit_price": price,
                    "total": quantity * price
                    if quantity is not None and price is not None
                    else None,
                    "linked": resolved.get("state") == "matched",
                }
            )
    items_total = sum((i["total"] for i in items if i["total"] is not None), Decimal(0))
    live = [o for o in orders if not native_ingest._is_cancelled(o)] or orders
    charges = native_ingest.ml_charges(db, store, number, live) if channel == "ml" else None
    if channel == "ml":
        # Mercado Livre: products + freight the buyer paid - seller's coupon, as Bling keeps it.
        order_total = (
            None if charges is None else items_total + charges["freight"] - charges["coupon"]
        )
    else:
        headers = [_amount(o.get("paid") or o.get("total")) for o in orders]
        order_total = None if any(h is None for h in headers) else sum(headers, Decimal(0))
    gap = None if order_total is None or charges else order_total - items_total
    freight = charges["freight"] if charges else (gap if gap is not None and gap > 0 else None)
    discount = charges["coupon"] if charges else (-gap if gap is not None and gap < 0 else None)
    return {
        "number": str(number),
        "channel": channel,
        "store_id": store,
        "store": names.get((channel, store), store),
        "orders": sorted(unit),
        "created_at": min((o.get("created_at") or "") for o in orders),
        "status": ", ".join(sorted({str(o.get("status") or "") for o in orders})),
        "fulfilled_by": ", ".join(
            sorted({str(o.get("fulfilled_by") or "") for o in orders} - {""})
        ),
        "items": items,
        "item_count": len(items),
        "quantity_sum": sum(
            (i["quantity"] for i in items if i["quantity"] is not None), Decimal(0)
        ),
        "items_total": items_total,
        "order_total": order_total,
        "adjustment": None if order_total is None else order_total - items_total,
        "freight": freight,
        "discount": discount,
        "buyer": (captured or {}).get("buyer") or {},
        "recipient": (captured or {}).get("recipient") or {},
        "missing": (captured or {}).get("missing")
        or ([] if captured else ["comprador não guardado"]),
        "captured_at": (captured or {}).get("captured_at") or "",
    }


def as_json(sale):
    def plain(value):
        if isinstance(value, Decimal):
            return f"{value:.2f}"
        if isinstance(value, dict):
            return {k: plain(v) for k, v in value.items()}
        if isinstance(value, list):
            return [plain(v) for v in value]
        return value

    out = plain(sale)
    for item, source in zip(out["items"], sale["items"], strict=True):
        item["quantity"] = None if source["quantity"] is None else _qty(source["quantity"])
    out["quantity_sum"] = _qty(sale["quantity_sum"])
    return out


def _sales(db, items):
    return [detail(db, *pick) for pick in picks(items)]


def _csv(header, rows):
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";")
    writer.writerow(header)
    writer.writerows(rows)
    return "﻿" + out.getvalue()


def _day(moment):
    return "/".join(reversed(moment[:10].split("-"))) if moment else ""


def sheet_csv(db, items):
    rows = []
    for s in _sales(db, items):
        for i in s["items"] or [{}]:
            rows.append(
                [
                    s["number"],
                    _day(s["created_at"]),
                    s["store"],
                    s["status"],
                    s["buyer"].get("name", ""),
                    s["buyer"].get("document", ""),
                    i.get("sku") or i.get("seller_sku", ""),
                    i.get("name", ""),
                    _qty(i.get("quantity")),
                    _money(i.get("unit_price")),
                    _money(i.get("total")),
                    _money(s["order_total"]),
                ]
            )
    return _csv(
        [
            "Número",
            "Data",
            "Loja",
            "Situação na loja",
            "Cliente",
            "CPF/CNPJ",
            "Código",
            "Descrição",
            "Quantidade",
            "Preço un",
            "Preço total",
            "Total da venda",
        ],
        rows,
    )


def addresses_csv(db, items):
    rows = []
    for s in _sales(db, items):
        r = s["recipient"]
        rows.append(
            [
                s["number"],
                s["store"],
                r.get("name") or s["buyer"].get("name", ""),
                s["buyer"].get("document", ""),
                r.get("street", ""),
                r.get("number", ""),
                r.get("complement", ""),
                r.get("district", ""),
                r.get("city", ""),
                r.get("state", ""),
                r.get("postal_code", ""),
                r.get("phone", ""),
            ]
        )
    return _csv(
        [
            "Número",
            "Loja",
            "Destinatário",
            "CPF/CNPJ",
            "Endereço",
            "Número",
            "Complemento",
            "Bairro",
            "Cidade",
            "UF",
            "CEP",
            "Telefone",
        ],
        rows,
    )


def _page(title, body):
    return (
        '<!doctype html><html lang="pt-BR"><meta charset="utf-8">'
        f"<title>DaVinci · {escape(title)}</title>"
        '<link rel="stylesheet" href="/assets/print.css">'
        f"<h1>DaVinci · {escape(title)}</h1>{body}"
    )


def production_html(db, items):
    sales, totals = _sales(db, items), {}
    for s in sales:
        for i in s["items"]:
            key = i["sku"] or i["seller_sku"] or "sem código"
            entry = totals.setdefault(
                key, {"name": i["name"], "quantity": Decimal(0), "sales": set()}
            )
            entry["quantity"] += i["quantity"] or 0
            entry["sales"].add(s["number"])
    rows = "".join(
        f"<tr><td>{escape(sku)}</td><td>{escape(t['name'] or 'Sem vínculo com o catálogo')}</td>"
        f"<td>{_qty(t['quantity'])}</td><td>{len(t['sales'])}</td></tr>"
        for sku, t in sorted(totals.items())
    )
    return _page(
        "Relatório de produção",
        f"<p>{len(sales)} vendas selecionadas. Quantidades somadas por código para separar.</p>"
        "<table><thead><tr><th>Código</th><th>Descrição</th><th>Quantidade</th>"
        f"<th>Vendas</th></tr></thead><tbody>{rows}</tbody></table>",
    )


def _address(r):
    parts = [
        ", ".join(p for p in [r.get("street"), r.get("number")] if p),
        r.get("complement"),
        r.get("district"),
        " / ".join(p for p in [r.get("city"), r.get("state") or r.get("uf")] if p),
        "CEP " + (r.get("postal_code") or r.get("zip"))
        if r.get("postal_code") or r.get("zip")
        else "",
    ]
    return escape(" · ".join(p for p in parts if p)) or "________________________________"


def _sender(db, s):
    from app.erp_local import multi_company

    skus = [{"sku": i["sku"]} for i in s["items"] if i["sku"]]
    order = {"channel": s["channel"], "store": s["store_id"], "items": skus}
    company_id = multi_company.route_for_order(db, order)
    row = (
        db.execute("SELECT data FROM fiscal_companies WHERE id=?", (company_id,)).fetchone()
        if company_id
        else None
    )
    return json.loads(row["data"]) if row else None


def declaration_html(db, items):
    pages = []
    for s in _sales(db, items):
        sender, r = _sender(db, s), s["recipient"]
        to_name = r.get("name") or s["buyer"].get("name") or "________________________________"
        blank = "________________________________"
        sender_html = (
            f"{escape(sender.get('name') or '')}<br>CNPJ "
            f"{escape(sender.get('cnpj', ''))}<br>{_address(sender.get('address') or {})}"
            if sender
            else f"Nome: {blank}<br>CPF/CNPJ: {blank}<br>Endereço: {blank}"
        )
        lines = "".join(
            f"<tr><td>{n}</td><td>{escape(i['name'] or i['seller_sku'])}</td>"
            f"<td>{_qty(i['quantity'])}</td><td>{_money(i['total'])}</td></tr>"
            for n, i in enumerate(s["items"], 1)
        )
        total = s["order_total"] if s["order_total"] is not None else s["items_total"]
        pages.append(
            '<section class="declaration">'
            f"<h2>Declaração de conteúdo · venda {escape(s['number'])}</h2>"
            "<table><tbody>"
            f"<tr><th>Remetente</th><td>{sender_html}</td></tr>"
            f"<tr><th>Destinatário</th><td>{escape(to_name)}"
            f"<br>CPF/CNPJ {escape(s['buyer'].get('document') or blank)}<br>{_address(r)}</td></tr>"
            "</tbody></table>"
            "<table><thead><tr><th>Item</th><th>Conteúdo</th><th>Quant.</th><th>Valor (R$)</th>"
            f'</tr></thead><tbody>{lines}<tr><th colspan="2">Totais</th>'
            f"<th>{_qty(s['quantity_sum'])}</th><th>{_money(total)}</th></tr></tbody></table>"
            "<p>Declaro que o conteúdo acima corresponde ao que está sendo enviado e que não "
            "envio objeto proibido pela legislação postal.</p>"
            f"<p>Data: ____/____/______ &nbsp; Assinatura: {blank}</p>"
            "</section>"
        )
    return _page("Declarações de conteúdo", "".join(pages))
