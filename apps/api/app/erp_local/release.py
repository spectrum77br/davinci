"""Offline readiness, consistent backup and restore rehearsal for the own ledger.

Passing these checks permits more local testing, not production activation.
No configuration/secret imports, credential export, schema changes or deployment.
"""

import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
from collections import defaultdict
from contextlib import closing
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from app.erp_local.core import ERPError

PRODUCTION_GAPS = [
    "Integrar o núcleo ao controle de acesso e às permissões do Davinci.",
    "Migrar e validar a persistência transacional no banco da aplicação.",
    "Conciliar saldos, depósitos, lotes e pedidos anteriores à abertura.",
    "Homologar pedidos, etiquetas, remessas e estoque por conta de cada canal.",
    "Homologar NF-e com certificado, cálculo, autorização, cancelamento e rejeições.",
    "Validar operação assistida, restauração e troca de autoridade por loja.",
]


def connect(path):
    path = Path(path).resolve(strict=True)
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    return db


def inspect_database(path):
    issues = []
    with closing(connect(path)) as db:
        db.execute("BEGIN")
        if [r[0] for r in db.execute("PRAGMA integrity_check")] != ["ok"]:
            issues.append("integrity")
        if db.execute("PRAGMA foreign_key_check").fetchone():
            issues.append("foreign_keys")
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required = {
            "erp_local_meta",
            "products",
            "balances",
            "documents",
            "movements",
            "events",
            "logistics_shipments",
            "logistics_labels",
            "logistics_manifests",
        }
        if not required <= tables:
            raise ERPError("foreign_database", "Arquivo incompatível com o núcleo próprio.")
        if [r[0] for r in db.execute("SELECT version FROM erp_local_meta")] != [1]:
            issues.append("schema_version")
        totals = defaultdict(lambda: [Decimal(0), Decimal(0)])
        for r in db.execute("SELECT sku,depot,physical_delta,reserved_delta FROM movements"):
            totals[r["sku"], r["depot"]][0] += Decimal(r["physical_delta"])
            totals[r["sku"], r["depot"]][1] += Decimal(r["reserved_delta"])
        balances = {
            (r["sku"], r["depot"]): [Decimal(r["physical"]), Decimal(r["reserved"])]
            for r in db.execute("SELECT * FROM balances")
        }
        if any(
            totals[k] != balances.get(k, [Decimal(0), Decimal(0)])
            for k in totals.keys() | balances.keys()
        ):
            issues.append("stock_ledger_mismatch")
        if any(v[1] < 0 for v in balances.values()):
            issues.append("negative_reservation")
        if db.execute("SELECT 1 FROM events WHERE result IS NULL LIMIT 1").fetchone():
            issues.append("unfinished_event")
        status_for = {
            "picking": "reserved",
            "packed": "reserved",
            "dispatched": "shipped",
            "returned": "returned",
            "cancelled": "cancelled",
        }
        for row in db.execute(
            "SELECT s.stage,d.status,s.data,d.data AS order_data "
            "FROM logistics_shipments s JOIN documents d ON d.id=s.id"
        ):
            if status_for.get(row["stage"]) != row["status"]:
                issues.append("shipping_order_mismatch")
            if (
                json.loads(row["data"])["requirements"]
                != json.loads(row["order_data"])["requirements"]
            ):
                issues.append("shipping_requirements_mismatch")
        if "product_structures" in tables:
            from app.erp_local import product_structures

            products, recipes = product_structures.graph(db)
            for row in db.execute("SELECT * FROM product_structures"):
                data = json.loads(row["data"])
                latest = db.execute(
                    "SELECT version,data FROM product_structure_history "
                    "WHERE sku=? ORDER BY version DESC LIMIT 1",
                    (row["sku"],),
                ).fetchone()
                if (
                    not latest
                    or latest["version"] != row["version"]
                    or json.loads(latest["data"]) != {"version": row["version"], **data}
                ):
                    issues.append("product_structure_history_mismatch")
                try:
                    flat = product_structures.expand(products, recipes, row["sku"])
                    if (
                        flat != data["expanded"]
                        or products[row["sku"]]["kind"] == "kit"
                        and flat != json.loads(products[row["sku"]]["components"])
                    ):
                        issues.append("product_structure_expansion_mismatch")
                except ERPError:
                    issues.append("product_structure_invalid")
        if {"catalog_image_assets", "product_images", "product_image_history"} <= tables:
            asset_ids = set()
            for row in db.execute("SELECT id,digest,size,body FROM catalog_image_assets"):
                asset_ids.add(row["id"])
                if (
                    len(row["body"]) != row["size"]
                    or hashlib.sha256(row["body"]).hexdigest() != row["digest"]
                ):
                    issues.append("catalog_image_digest_mismatch")
            for row in db.execute("SELECT * FROM product_images"):
                latest = db.execute(
                    "SELECT version,data FROM product_image_history "
                    "WHERE sku=? ORDER BY version DESC LIMIT 1",
                    (row["sku"],),
                ).fetchone()
                if (
                    not latest
                    or latest["version"] != row["version"]
                    or latest["data"] != row["data"]
                ):
                    issues.append("product_image_history_mismatch")
            for row in db.execute("SELECT data FROM product_image_history"):
                if any(image["id"] not in asset_ids for image in json.loads(row[0])):
                    issues.append("product_image_asset_missing")
        if {"product_tax", "product_tax_history"} <= tables:
            for row in db.execute("SELECT * FROM product_tax"):
                latest = db.execute(
                    "SELECT version,data FROM product_tax_history "
                    "WHERE sku=? ORDER BY version DESC LIMIT 1",
                    (row["sku"],),
                ).fetchone()
                if (
                    not latest
                    or latest["version"] != row["version"]
                    or latest["data"] != row["data"]
                ):
                    issues.append("product_tax_history_mismatch")
        if {"product_suppliers", "product_supplier_history"} <= tables:
            for row in db.execute("SELECT * FROM product_suppliers"):
                latest = db.execute(
                    "SELECT version,data FROM product_supplier_history "
                    "WHERE sku=? ORDER BY version DESC LIMIT 1",
                    (row["sku"],),
                ).fetchone()
                if (
                    not latest
                    or latest["version"] != row["version"]
                    or latest["data"] != row["data"]
                ):
                    issues.append("product_supplier_history_mismatch")
        if "stock_allocation_history" in tables:
            for row in db.execute("SELECT id,data FROM documents WHERE kind='order'"):
                data = json.loads(row["data"])
                allocation = data.get("stock_allocation")
                if not allocation:
                    continue
                chosen = db.execute(
                    "SELECT data FROM stock_allocation_history WHERE order_id=? AND version=?",
                    (row["id"], allocation["version"]),
                ).fetchone()
                frozen = json.loads(chosen[0]) if chosen else {}
                if (
                    not chosen
                    or frozen.get("requirements") != data["requirements"]
                    or frozen.get("fulfillment_items") != data.get("fulfillment_items")
                    or frozen.get("commercial_items") != data["items"]
                    or frozen.get("fulfillment_product_snapshots")
                    != data.get("fulfillment_product_snapshots")
                    or frozen.get("fiscal_allocation_review")
                    != data.get("fiscal_allocation_review")
                ):
                    issues.append("stock_allocation_history_mismatch")
        if "purchase_document_history" in tables:
            from app.erp_local.purchase_documents import commercial

            for row in db.execute("SELECT id,data FROM documents WHERE kind='purchase'"):
                data = json.loads(row["data"])
                if not data.get("version"):
                    continue
                latest = db.execute(
                    "SELECT version,data FROM purchase_document_history "
                    "WHERE purchase_id=? ORDER BY version DESC LIMIT 1",
                    (row["id"],),
                ).fetchone()
                if (
                    not latest
                    or latest["version"] != data["version"]
                    or json.loads(latest["data"]) != commercial(data)
                ):
                    issues.append("purchase_history_mismatch")
        if "commercial_proposals" in tables:
            proposal_links = {}
            for row in db.execute("SELECT * FROM commercial_proposals"):
                data = json.loads(row["data"])
                latest = db.execute(
                    "SELECT version,data FROM commercial_proposal_history "
                    "WHERE proposal_id=? ORDER BY version DESC LIMIT 1",
                    (row["id"],),
                ).fetchone()
                if (
                    not latest
                    or latest["version"] != data["version"]
                    or json.loads(latest["data"]) != {**data, "status": row["status"]}
                ):
                    issues.append("proposal_history_mismatch")
                linked = data.get("linked_sale")
                if bool(linked) != (row["status"] == "converted"):
                    issues.append("proposal_sale_mismatch")
                if linked:
                    proposal_links[row["id"]] = linked
                    sale = db.execute(
                        "SELECT kind,data FROM documents WHERE id=?", (linked,)
                    ).fetchone()
                    if (
                        not sale
                        or sale["kind"] != "order"
                        or json.loads(sale["data"]).get("proposal", {}).get("id") != row["id"]
                    ):
                        issues.append("proposal_sale_mismatch")
            for row in db.execute("SELECT id,data FROM documents WHERE kind='order'"):
                source = json.loads(row["data"]).get("proposal")
                if source and proposal_links.get(source["id"]) != row["id"]:
                    issues.append("proposal_sale_mismatch")
        if "cash_sessions" in tables:
            from app.erp_local.cash_register import summaries
            from app.erp_local.core import LocalERP

            for row in db.execute("SELECT * FROM cash_sessions"):
                data = json.loads(row["data"])
                current = summaries(LocalERP, db, row["id"])
                if Decimal(current["cash_expected"]) < 0:
                    issues.append("cash_negative_expected")
                if row["status"] in {"closed", "verified"}:
                    closure = data.get("closure")
                    expected = {g["method_id"]: g["expected"] for g in current["groups"]}
                    if not closure:
                        issues.append("cash_closure_missing")
                    elif current["pending"] or expected != closure["expected"]:
                        issues.append("cash_closed_sales_changed")
        counts = {
            name: db.execute(query).fetchone()[0]
            for name, query in {
                "products": "SELECT count(*) FROM products",
                "documents": "SELECT count(*) FROM documents",
                "events": "SELECT count(*) FROM events",
                "movements": "SELECT count(*) FROM movements",
                "logistics_shipments": "SELECT count(*) FROM logistics_shipments",
                "logistics_labels": "SELECT count(*) FROM logistics_labels",
                "logistics_manifests": "SELECT count(*) FROM logistics_manifests",
            }.items()
        }
    return {
        "checked_at": datetime.now(UTC).isoformat(),
        "ledger_ok": not issues,
        "issues": sorted(set(issues)),
        "counts": counts,
        "production_ready": False,
        "external_writes": False,
        "production_gaps": PRODUCTION_GAPS,
    }


def digest(path):
    with Path(path).open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def backup(source, destination):
    source, destination = Path(source).resolve(strict=True), Path(destination).absolute()
    destination.parent.mkdir(parents=True, exist_ok=True)
    manifest_path = destination.with_suffix(destination.suffix + ".json")
    if destination.exists() or destination.is_symlink() or manifest_path.exists():
        raise ERPError("backup_exists", "Escolha um novo nome de cópia.", 409)
    descriptor, temporary = tempfile.mkstemp(prefix=".erp-backup-", dir=destination.parent)
    os.close(descriptor)
    try:
        with closing(connect(source)) as live, closing(sqlite3.connect(temporary)) as target:
            live.backup(target)
            target.execute("PRAGMA journal_mode=DELETE")
        with Path(temporary).open("rb") as file:
            os.fsync(file.fileno())
        report = inspect_database(temporary)
        if not report["ledger_ok"]:
            raise ERPError(
                "backup_invalid", "A cópia apresenta divergências; verifique o relatório."
            )
        manifest = {
            "format": "davinci-own-ledger-backup-v1",
            "created_at": report["checked_at"],
            "sha256": digest(temporary),
            "size": Path(temporary).stat().st_size,
            "counts": report["counts"],
        }
        with manifest_path.open("x") as file:
            os.chmod(manifest_path, 0o600)
            json.dump(manifest, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        # Link is exclusive: an existing destination is never replaced.
        os.link(temporary, destination)
        return {"backup": str(destination), "manifest": str(manifest_path), **manifest}
    finally:
        Path(temporary).unlink(missing_ok=True)


def restore(source, destination):
    source = Path(source).resolve(strict=True)
    manifest = json.loads(source.with_suffix(source.suffix + ".json").read_text())
    if (
        manifest.get("format") != "davinci-own-ledger-backup-v1"
        or manifest.get("sha256") != digest(source)
        or manifest.get("size") != source.stat().st_size
    ):
        raise ERPError("backup_checksum", "A cópia não corresponde ao manifesto.")
    report = backup(source, destination)
    if report["counts"] != manifest.get("counts"):
        raise ERPError("backup_counts", "A restauração diverge das contagens da cópia.")
    return {**report, "restore_verified": True}


def main():
    parser = argparse.ArgumentParser(description="Verificação e restauração do ERP próprio local")
    parser.add_argument("action", choices=["check", "backup", "restore"])
    parser.add_argument("source")
    parser.add_argument("destination", nargs="?")
    args = parser.parse_args()
    if args.action != "check" and not args.destination:
        parser.error("informe um novo arquivo de destino")
    result = (
        inspect_database(args.source)
        if args.action == "check"
        else {"backup": backup, "restore": restore}[args.action](args.source, args.destination)
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ledger_ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
