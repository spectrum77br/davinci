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


def local_sales(db):
    """What the ERP would turn into orders, in the shape the comparison needs."""
    sales = {}
    for channel, store in db.execute(
        "SELECT DISTINCT channel,store FROM native_order_observations"
    ):
        for key, unit in native_ingest._units(db, channel, store).items():
            live = [o for o in unit.values() if not native_ingest._is_cancelled(o)]
            if not live:
                continue
            items, total = Counter(), Decimal(0)
            for order in live:
                total += Decimal(str((order.get("total") or {}).get("amount") or 0))
                for item in order.get("items") or []:
                    resolved = native_ingest._resolve(db, store, item, channel)
                    items[resolved["sku"] or item.get("seller_sku") or "?"] += (
                        native_ingest._quantity(channel, item) or 0
                    )
            captured = native_ingest._unit_recipient(db, channel, store, unit)
            buyer = (captured or {}).get("buyer") or {}
            delivery = (captured or {}).get("recipient") or {}
            sales[key] = {
                "channel": channel,
                "items": dict(items),
                "total": str(total),
                "document": fingerprint(buyer.get("document"), digits=True),
                "name": fingerprint(delivery.get("name")),
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
        entry[
            "total_igual"
            if Decimal(sale["total"]) == Decimal(str(other["total"]))
            else "total_diferente"
        ] += 1
        if not sale["document"]:
            entry["sem_comprador_no_erp"] += 1
            continue
        for field, label in (("document", "documento"), ("name", "nome"), ("postal_code", "cep")):
            entry[f"{label}_{'igual' if sale[field] == other[field] else 'diferente'}"] += 1
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
