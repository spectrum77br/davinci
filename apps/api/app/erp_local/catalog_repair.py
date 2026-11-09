"""Audited completion of MISSING opening values, never a working-stock refresh."""

import hashlib
import json
from collections import defaultdict
from decimal import Decimal, InvalidOperation

from app.erp_local.core import ERPError, dumps, number
from app.erp_local.live_validation import validate_live_read


def repair_missing_catalog(erp, live_read):
    read = validate_live_read(live_read)
    digest = hashlib.sha256(dumps(read).encode()).hexdigest()
    key = "catalog-repair-" + digest
    with erp.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        previous = db.execute("SELECT result FROM events WHERE operation_key=?", (key,)).fetchone()
        if previous:
            return {**json.loads(previous[0]), "replayed": True}
        imported = db.execute("SELECT data FROM catalog_import WHERE id=1").fetchone()
        if not imported:
            raise ERPError("opening_required", "A correção exige uma carga real já existente.")
        report = json.loads(imported[0])
        origins = {
            r["sku"]: json.loads(r["data"]) for r in db.execute("SELECT * FROM catalog_origin")
        }
        products = {r["sku"]: dict(r) for r in db.execute("SELECT * FROM products")}
        by_id = defaultdict(list)
        for sku, origin in origins.items():
            by_id[origin["bling_product_id"]].append(sku)
        structures = {r["id"]: r for r in read["structures"]}
        balances = {r["id"]: r for r in read["balances"]}
        result = {
            "balances_completed": [],
            "structures_completed": [],
            "identities_confirmed": [],
            "blocked": [],
            "captured_at": read["captured_at"],
            "read_digest": digest,
            "external_writes": False,
        }
        db.execute(
            "INSERT INTO events(operation_key,fingerprint,kind,actor,payload,created_at) "
            "VALUES (?,?,?,?,?,?)",
            (
                key,
                digest,
                "catalog.complete_missing",
                "conciliação local",
                dumps({"read_digest": digest}),
                erp.now(),
            ),
        )

        def flatten(sku, stack):
            p, origin = products[sku], origins[sku]
            identity = origin["bling_product_id"]
            if identity in stack:
                raise ERPError("kit_cycle", "Ciclo na composição.")
            if origin["catalog_state"] != "ready" and p["kind"] == "simple":
                raise ERPError("component_identity", "Componente pendente de identidade.")
            if p["kind"] == "simple":
                return {sku: Decimal(1)}
            if origin["catalog_state"] == "ready":
                return {
                    part["sku"]: Decimal(part["quantity"]) for part in json.loads(p["components"])
                }
            fresh = structures.get(identity)
            if (
                not fresh
                or fresh["sku"] != sku
                or fresh["format"] != "E"
                or fresh["status"] != "A"
                or not fresh["components"]
            ):
                raise ERPError("missing_structure", "Composição completa ainda ausente.")
            output = defaultdict(Decimal)
            for part in fresh["components"]:
                matches = by_id[part["id"]]
                if len(matches) != 1:
                    raise ERPError(
                        "component_identity",
                        "Identificador do componente não é único na base própria.",
                    )
                quantity = number(part["quantity"])
                for component, count in flatten(matches[0], stack | {identity}).items():
                    output[component] += quantity * count
            return dict(output)

        for sku, origin in origins.items():
            if products[sku]["kind"] != "simple" or origin["stock_state"] != "missing":
                continue
            fresh = balances.get(origin["bling_product_id"])
            if not fresh:
                continue
            if (
                db.execute("SELECT 1 FROM movements WHERE sku=? LIMIT 1", (sku,)).fetchone()
                or db.execute("SELECT 1 FROM balances WHERE sku=? LIMIT 1", (sku,)).fetchone()
            ):
                result["blocked"].append({"sku": sku, "code": "working_stock_exists"})
                continue
            try:
                physical, available = Decimal(fresh["physical"]), Decimal(fresh["available"])
                reserved = physical - available
                if (
                    not all(
                        n.is_finite() and abs(n) <= Decimal("9999999")
                        for n in (physical, available, reserved)
                    )
                    or reserved < 0
                ):
                    raise InvalidOperation
            except (InvalidOperation, TypeError, ValueError):
                result["blocked"].append({"sku": sku, "code": "invalid_opening"})
                continue
            db.execute(
                "INSERT INTO balances VALUES (?,?,?,?)",
                (sku, "geral", str(physical), str(reserved)),
            )
            db.execute(
                "INSERT INTO movements(operation_key,sku,depot,physical_delta,reserved_delta,"
                "reason,created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    key,
                    sku,
                    "geral",
                    str(physical),
                    str(reserved),
                    "complemento de abertura ausente",
                    erp.now(),
                ),
            )
            origin.update(
                stock_state="known",
                stock_basis="bling_api_missing_completion",
                stock_recovery_at=read["captured_at"],
                stock_read_digest=digest,
            )
            result["balances_completed"].append(sku)
        for issue in report["issues"]:
            sku = issue["sku"]
            if (
                issue["code"] != "component_identity"
                or sku not in products
                or products[sku]["kind"] != "simple"
            ):
                continue
            origin = origins[sku]
            fresh = structures.get(origin["bling_product_id"])
            if fresh and fresh["sku"] == sku and fresh["format"] == "S" and fresh["status"] == "A":
                origin.update(catalog_state="ready", identity_read_digest=digest)
                result["identities_confirmed"].append(sku)
        eligible = {i["sku"] for i in report["issues"] if i["code"] == "missing_structure"}
        for sku in sorted(eligible):
            if sku not in products or origins[sku]["bling_product_id"] not in structures:
                continue
            try:
                parts = [
                    {"sku": s, "quantity": str(number(q))}
                    for s, q in sorted(flatten(sku, set()).items())
                ]
                if not parts:
                    raise ERPError("missing_structure", "Composição vazia.")
                # Already-created document requirements remain frozen, even on a later repair.
                db.execute("UPDATE products SET components=? WHERE sku=?", (dumps(parts), sku))
                products[sku]["components"] = dumps(parts)
                origins[sku].update(catalog_state="ready", structure_read_digest=digest)
                result["structures_completed"].append(sku)
            except ERPError as exc:
                result["blocked"].append({"sku": sku, "code": exc.code})
        for sku, origin in origins.items():
            db.execute("UPDATE catalog_origin SET data=? WHERE sku=?", (dumps(origin), sku))
        completed = set(result["structures_completed"])
        confirmed = set(result["identities_confirmed"])
        report["issues"] = [
            i
            for i in report["issues"]
            if not (
                (i["code"] == "missing_structure" and i["sku"] in completed)
                or (i["code"] == "component_identity" and i["sku"] in confirmed)
            )
        ]
        report["known_stock"] += len(result["balances_completed"])
        report["unknown_stock"] -= len(result["balances_completed"])
        report["direct_stock"] += len(result["balances_completed"])
        report.setdefault("completions", []).append(result)
        db.execute("UPDATE catalog_import SET data=? WHERE id=1", (dumps(report),))
        db.execute("UPDATE events SET result=? WHERE operation_key=?", (dumps(result), key))
        from app.erp_local.channels import capture_stock_changes

        capture_stock_changes(erp, db, key)
        db.commit()
        return {**result, "replayed": False}
