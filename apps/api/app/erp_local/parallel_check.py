"""Each sale the ERP read, side by side with what Bling holds for it. Counts only leave here.

Buyer identity is compared as hashes made on the host where the data lives, so names and
documents never travel. A SKU that only changed sale lot (.ci, .sp...) is counted apart: the
stock priority robot does that on purpose.
"""

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

from app.erp_local import native_ingest, stock_families
from app.erp_local.core import LocalERP, dumps


def fingerprint(value, *, digits=False):
    text = str(value or "").strip()
    text = "".join(c for c in text if c.isdigit()) if digits else " ".join(text.casefold().split())
    return hashlib.sha256(text.encode()).hexdigest()[:16] if text else ""


def robot_view(db):
    """DaVinci's lot priorities and Bling's available balance, from the read-only reference."""
    import sqlite3

    from app.erp_local import stock_priority

    try:
        rows = {
            kind: [
                json.loads(r[0])
                for r in db.execute("SELECT data FROM reference_rows WHERE kind=?", (kind,))
            ]
            for kind in ("priorities", "products")
        }
    except sqlite3.OperationalError:
        return {}, None
    available = {
        str(p.get("sku") or "").lower(): p.get("stock")
        for p in rows["products"]
        if p.get("sku") and p.get("stock") is not None
    }
    return stock_priority.map_from_rows(rows["priorities"]), available


def _is_flex(db, channel, store, key, orders):
    """Mercado Livre Flex: the shipment read from the store is self_service."""
    if any(
        o.get("logistic_type") in {"self_service", "flex"} or o.get("fulfillment") == "flex"
        for o in orders
    ):
        return True
    if channel != "ml":
        return False
    row = db.execute(
        "SELECT data FROM store_tracking WHERE channel='ml' AND store=? AND number=?",
        (store, key),
    ).fetchone()
    return bool(row) and json.loads(row["data"]).get("carrier") == "self_service"


def local_sales(db, mapping=None, available=None):
    """What the ERP would turn into orders, in the shape the comparison needs.

    With DaVinci's priorities, each item goes to the lot the robot would choose, as the ERP will
    do once it owns the store.
    """
    from app.erp_local import stock_priority

    sales = {}
    for channel, store in db.execute(
        "SELECT DISTINCT channel,store FROM native_order_observations"
    ):
        for key, unit in native_ingest._units(db, channel, store).items():
            live = [o for o in unit.values() if not native_ingest._is_cancelled(o)]
            if not live:
                continue
            items, total = Counter(), Decimal(0)
            # Mercado Livre: products + freight the buyer paid - coupon, once both were read.
            charges = native_ingest.store_charges(db, channel, store, key, live)
            unread = channel in native_ingest.CHARGED and charges is None
            lines, unresolved = [], Counter()
            for order in live:
                if charges is None:
                    header = order.get("paid") or order.get("total") or {}
                    total += Decimal(str(header.get("amount") or 0))
                for item in order.get("items") or []:
                    resolved = native_ingest._resolve(db, store, item, channel)
                    quantity = native_ingest._quantity(channel, item) or 0
                    if charges is not None:
                        price = native_ingest._price(item) or {}
                        total += Decimal(str(price.get("amount") or 0)) * Decimal(str(quantity))
                    if resolved["sku"]:
                        lines.append((resolved["sku"], quantity))
                    else:
                        unresolved[item.get("seller_sku") or "?"] += quantity
            # The lot the robot would choose for the whole sale: Flex, one lot, then priority.
            chosen = (
                stock_priority.preferred_order(
                    lines, mapping, available, flex=_is_flex(db, channel, store, key, live)
                )
                if mapping is not None
                else [sku for sku, _ in lines]
            )
            for sku, (_, quantity) in zip(chosen, lines, strict=True):
                items[sku] += quantity
            items.update(unresolved)
            captured = native_ingest._unit_recipient(db, channel, store, unit)
            buyer = (captured or {}).get("buyer") or {}
            delivery = (captured or {}).get("recipient") or {}
            sales[key] = {
                "channel": channel,
                "items": dict(items),
                "total": None
                if unread
                else str(total + charges["freight"] - charges["coupon"] if charges else total),
                "document": fingerprint(buyer.get("document"), digits=True),
                "name": fingerprint(delivery.get("name")),
                "buyer_name": fingerprint(buyer.get("name")),
                "postal_code": fingerprint(delivery.get("postal_code"), digits=True),
            }
    return sales


def compare(local, bling):
    report = defaultdict(Counter)
    for key, sale in local.items():
        entry = report[sale["channel"]]
        entry["vendas"] += 1
        other = bling.get(key)
        if not other:
            entry["fora_do_bling"] += 1
            continue
        entry["no_bling"] += 1
        mine = {sku: Decimal(str(q)) for sku, q in sale["items"].items()}
        theirs = {sku: Decimal(str(q)) for sku, q in other["items"].items()}
        if mine == theirs:
            entry["itens_iguais"] += 1
        elif _by_family(mine) == _by_family(theirs):
            entry["itens_iguais_com_troca_de_lote"] += 1
        else:
            entry["itens_diferentes"] += 1
        if sale["total"] is None:
            entry["total_sem_frete_lido"] += 1
        else:
            entry[
                "total_igual"
                if Decimal(sale["total"]) == Decimal(str(other["total"]))
                else "total_diferente"
            ] += 1
        if not sale["document"]:
            entry["sem_comprador_no_erp"] += 1
            continue
        for field, label in (("document", "documento"), ("name", "nome"), ("postal_code", "cep")):
            # An empty field on Bling's copy is a gap there, not a difference.
            if not other[field]:
                entry[f"{label}_vazio_no_bling"] += 1
                continue
            mine = {sale[field], sale.get("buyer_name")} if field == "name" else {sale[field]}
            entry[f"{label}_{'igual' if other[field] in mine else 'diferente'}"] += 1
    return {channel: dict(counts) for channel, counts in report.items()}


def _by_family(items):
    grouped = Counter()
    for sku, quantity in items.items():
        grouped[stock_families.key(sku)] += quantity
    return grouped


def main():
    parser = argparse.ArgumentParser(description="Conferência com o Bling, só contagens")
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--reference-ssh", default="davinci-prod")
    args = parser.parse_args()
    erp = LocalERP(args.database)
    with erp.connection() as db:
        local = local_sales(db)
    script = (Path(__file__).parent / "parallel_check_read.py").read_bytes()
    bling = {}
    keys = sorted(local)
    for start in range(0, len(keys), 500):
        bling.update(
            native_ingest.run_on_host(
                script, {"keys": keys[start : start + 500]}, alias=args.reference_ssh
            )
        )
    report = compare(local, bling)
    args.output.write_text(dumps(report) + "\n", "utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
