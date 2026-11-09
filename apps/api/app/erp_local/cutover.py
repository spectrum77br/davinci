"""Turnover from Bling, run once where the credentials and data live, after freezing the origin.

Open orders keep the ids DaVinci already stores, so its rows stay linked. Balances are reconciled
against what Bling reports, and every difference is listed rather than silently absorbed. Each
step is replay-safe. Nothing here writes to Bling or to any store.
"""

import hashlib
import json
import re
from decimal import Decimal
from pathlib import Path
from unicodedata import combining, normalize

from app.erp_local.core import ERPError, dumps

OPEN_SITUATIONS = {6, 15, 21}
CITIES = json.loads(Path(__file__).with_name("nfe_cities.json").read_text("utf-8"))["cities"]


def _fold(value):
    plain = "".join(c for c in normalize("NFD", str(value or "")) if not combining(c))
    return re.sub(r"[^a-z0-9]+", " ", plain.lower()).strip()


FOLDED = {uf: {_fold(name): code for name, code in rows.items()} for uf, rows in CITIES.items()}


def city_code(city, state):
    """Official municipality code. A name not found stays empty and blocks the invoice."""
    return FOLDED.get(state, {}).get(_fold(city), "")


def _stores(db):
    result = {}
    for row in db.execute("SELECT channel,store,data FROM store_policies"):
        profile = json.loads(row["data"]).get("source_profile") or {}
        if profile.get("bling_store_id"):
            result[str(profile["bling_store_id"])] = (row["channel"], row["store"])
    return result


def _contact(erp, raw, delivery):
    buyer = raw.get("contato") or {}
    document = re.sub(r"\D", "", str(buyer.get("numeroDocumento") or ""))
    if not document:
        return None
    key = "cutover-v1:contact:" + hashlib.sha256(document.encode()).hexdigest()
    state = delivery.get("uf") or ""
    code = city_code(delivery.get("municipio"), state)
    data = {
        "name": str(buyer.get("nome") or delivery.get("nome") or "Sem nome")[:120],
        "roles": ["customer"],
        "person_type": "J" if len(document) > 11 else "F",
        "document": document,
    }
    for field, value in [
        ("street", delivery.get("endereco")),
        ("number", delivery.get("numero")),
        ("complement", delivery.get("complemento")),
        ("district", delivery.get("bairro")),
        ("city", delivery.get("municipio")),
        ("state", state),
        ("postal_code", re.sub(r"\D", "", str(delivery.get("cep") or ""))),
        ("ibge_code", code),
    ]:
        if value:
            data[field] = value
    try:
        return erp.command(key, "contact.save", {"data": data})["id"]
    except ERPError as error:
        if error.code != "contact_duplicate":
            return None
    with erp.connection() as db:
        for row in db.execute("SELECT id,data FROM own_contacts"):
            if re.sub(r"\D", "", json.loads(row["data"]).get("document") or "") == document:
                return row["id"]
    return None


def import_open_orders(erp, raws, *, depot):
    """`raws` are full Bling orders. Closed ones stay history: DaVinci already mirrors them."""
    report = {"imported": 0, "closed": 0, "already": 0, "blocked": []}
    with erp.connection() as db:
        stores = _stores(db)
    for raw in raws:
        identifier = int(raw["id"])
        situation = int((raw.get("situacao") or {}).get("id") or 0)
        if situation not in OPEN_SITUATIONS:
            report["closed"] += 1
            continue
        with erp.connection() as db:
            if db.execute(
                "SELECT 1 FROM compat_ids WHERE kind='order' AND numeric_id=?", (identifier,)
            ).fetchone():
                report["already"] += 1
                continue
        store_id = str((raw.get("loja") or {}).get("id") or "")
        channel, store = stores.get(store_id, ("bling", store_id or "manual"))
        delivery = (raw.get("transporte") or {}).get("etiqueta") or {}
        items = [
            {
                "sku": str(i.get("codigo") or ""),
                "quantity": str(i.get("quantidade")),
                "unit_price": str(i.get("valor")),
            }
            for i in raw.get("itens") or []
        ]
        fees = raw.get("taxas") or {}
        payload = {
            "channel": channel,
            "store": store,
            "external_id": str(raw.get("numeroLoja") or raw["numero"]),
            "depot": depot,
            "items": items,
            "date": str(raw.get("data") or "")[:10] or None,
            "recipient": {
                "name": delivery.get("nome") or "",
                "street": delivery.get("endereco") or "",
                "number": delivery.get("numero") or "",
                "complement": delivery.get("complemento") or "",
                "district": delivery.get("bairro") or "",
                "city": delivery.get("municipio") or "",
                "state": delivery.get("uf") or "",
                "postal_code": delivery.get("cep") or "",
            },
            "channel_fees": {
                "base": str(fees.get("valorBase") or 0),
                "shipping_cost": str(fees.get("custoFrete") or 0),
                "commission": str(fees.get("taxaComissao") or 0),
            },
        }
        contact = _contact(erp, raw, delivery)
        if contact:
            payload["contact_id"] = contact
        else:
            payload["customer_name"] = str((raw.get("contato") or {}).get("nome") or "Sem nome")
        if raw.get("numeroPedidoCompra"):
            payload["purchase_order"] = str(raw["numeroPedidoCompra"])
        payload = {k: v for k, v in payload.items() if v is not None}
        try:
            created = erp.command(f"cutover-v1:order:{identifier}", "order.import", payload)
        except ERPError as error:
            report["blocked"].append({"id": identifier, "code": error.code})
            continue
        with erp.connection() as db:
            db.execute(
                "INSERT INTO compat_ids VALUES ('order',?,?) ON CONFLICT(kind,local_id) "
                "DO UPDATE SET numeric_id=excluded.numeric_id",
                (created["id"], identifier),
            )
            # DaVinci already holds this order: importing it is not news to announce.
            db.execute("DELETE FROM compat_outbox WHERE id=(SELECT max(id) FROM compat_outbox)")
            db.execute(
                "INSERT OR REPLACE INTO compat_order_situations VALUES (?,?,?,?)",
                (created["id"], situation, f"cutover-v1:order:{identifier}", erp.now()),
            )
        report["imported"] += 1
    return report


def _by_origin(db):
    result = {}
    for row in db.execute("SELECT sku,data FROM catalog_origin"):
        source = json.loads(row["data"]).get("bling_product_id")
        if source:
            result[str(source)] = row["sku"]
    return result


def apply_opening_balances(erp, balances, *, depot):
    """Set the physical stock Bling reports. Kits and services never hold their own balance."""
    report = {"adjusted": 0, "same": 0, "unknown": 0, "skipped_kits": 0, "refused": []}
    with erp.connection() as db:
        origin = _by_origin(db)
    for row in balances:
        sku = origin.get(str(row["produto"]["id"]))
        if not sku:
            report["unknown"] += 1
            continue
        target = Decimal(str(row.get("saldoFisicoTotal") or 0))
        with erp.connection() as db:
            product = erp.product(db, sku)
            physical, _ = erp.balance(db, sku, depot)
        if product["kind"] != "simple":
            report["skipped_kits"] += 1
            continue
        if physical == target:
            report["same"] += 1
            continue
        key = "cutover-v1:balance:" + hashlib.sha256(dumps([sku, str(target)]).encode()).hexdigest()
        try:
            erp.command(
                key,
                "stock.post",
                {
                    "sku": sku,
                    "depot": depot,
                    "type": "B",
                    "quantity": str(target),
                    "reason": "Abertura da virada: saldo físico informado pelo Bling",
                },
            )
        except ERPError as error:
            report["refused"].append({"sku": sku, "code": error.code})
            continue
        report["adjusted"] += 1
    return report


def compare_balances(erp, balances, *, depot):
    """Available here must equal Bling's virtual balance, or the reservations disagree."""
    differences = []
    with erp.connection() as db:
        origin = _by_origin(db)
        for row in balances:
            sku = origin.get(str(row["produto"]["id"]))
            if not sku or erp.product(db, sku)["kind"] != "simple":
                continue
            physical, reserved = erp.balance(db, sku, depot)
            expected = Decimal(str(row.get("saldoVirtualTotal") or 0))
            if physical - reserved != expected:
                differences.append(
                    {"sku": sku, "here": str(physical - reserved), "bling": str(expected)}
                )
    return {"checked": len(balances), "differences": differences}
