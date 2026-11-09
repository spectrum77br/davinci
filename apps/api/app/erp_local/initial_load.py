"""One-time real catalog/opening balance load into an EMPTY, independent ERP.

Reference refreshes never call this again or overwrite working balances.
Historic orders are reference data: importing them as new reservations would
double-count the opening reservation already included in the balances.
"""

import json
from collections import defaultdict
from decimal import Decimal, InvalidOperation

from app.erp_local.core import ERPError, dumps


def bootstrap_missing_prices(erp, reference):
    """Fill only previously absent opening prices; never update a working price."""
    prices = {p["id"]: p for p in reference.all_rows("products")}
    with erp.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            origins = db.execute(
                "SELECT o.sku,o.data FROM catalog_origin o LEFT JOIN product_pricing p "
                "ON p.sku=o.sku WHERE p.sku IS NULL"
            ).fetchall()
            for origin in origins:
                p = prices.get(json.loads(origin["data"])["source_id"])
                if p:
                    db.execute(
                        "INSERT INTO product_pricing VALUES (?,?,?)",
                        (
                            origin["sku"],
                            p["price"],
                            p["bling_cost_price"]
                            if p["bling_cost_price"] is not None
                            else p["cost_price"],
                        ),
                    )
            db.commit()
        except Exception:
            db.rollback()
            raise


def bootstrap_real_catalog(erp, reference, live_read=None):
    metadata = reference.info()["snapshot"]
    if not metadata:
        raise ERPError("reference_required", "Carregue a referência real primeiro.")
    rows = reference.all_rows("products")
    live_balances = {}
    if live_read:
        from app.erp_local.live_validation import validate_live_read

        live_read = validate_live_read(live_read)
        live_balances = {row["id"]: row for row in live_read["balances"]}
    groups, by_id, structures = defaultdict(list), defaultdict(list), defaultdict(list)
    for row in rows:
        groups[row["sku"]].append(row)
        by_id[row["bling_product_id"]].append(row)
    for row in reference.all_rows("components"):
        structures[row["kit_bling_product_id"]].append(row)
    selected = {}
    issues = []
    for sku, matches in groups.items():
        active = [p for p in matches if p["situacao"] == "A"]
        if len(active) == 1 and sku:
            selected[sku] = active[0]
        elif len(active) > 1:
            issues.append({"sku": sku, "code": "ambiguous_sku"})
    resolved, invalid = {}, set()

    def flatten(product, stack):
        identity = product["bling_product_id"]
        if identity in stack:
            raise ERPError("kit_cycle", "Ciclo na composição do kit.")
        if product["formato"] == "S":
            sku = product["sku"]
            if not sku or any(p["bling_product_id"] != identity for p in groups[sku]):
                raise ERPError("component_identity", "Componente com identidade ambígua.")
            selected.setdefault(sku, product)
            return {sku: Decimal(1)}
        if product["formato"] != "E" or not structures[identity]:
            raise ERPError("missing_structure", "Composição ainda não disponível.")
        output = defaultdict(Decimal)
        for part in structures[identity]:
            matches = by_id[part["component_bling_product_id"]]
            if len(matches) != 1:
                raise ERPError("component_identity", "Componente não localizado unicamente.")
            quantity = Decimal(part["quantity"])
            if not quantity.is_finite() or quantity <= 0:
                raise ERPError("invalid_component", "Quantidade de componente inválida.")
            for sku, count in flatten(matches[0], stack | {identity}).items():
                output[sku] += quantity * count
        return dict(output)

    for sku, product in list(selected.items()):
        try:
            resolved[sku] = flatten(product, set())
        except (ERPError, InvalidOperation) as e:
            invalid.add(sku)
            issues.append({"sku": sku, "code": getattr(e, "code", "invalid_component")})
    # Components added during flattening are simple products.
    report = {
        "source_digest": metadata["digest"],
        "source_captured_at": metadata["captured_at"],
        "loaded_at": erp.now(),
        "products": len(selected),
        "active_products": sum(p["situacao"] == "A" for p in selected.values()),
        "kits": sum(p["formato"] == "E" for p in selected.values()),
        "known_stock": 0,
        "unknown_stock": 0,
        "shortages": 0,
        "direct_stock": 0,
        "mirror_stock": 0,
        "initial_api_read": {
            "captured_at": live_read["captured_at"],
            "balances": len(live_balances),
            "calls": len(live_read["calls"]),
            "failures": live_read["failures"],
            "no_token_refresh": live_read["no_token_refresh"],
        }
        if live_read
        else None,
        "issues": issues,
        "reservation_basis": "opening_aggregate_not_historic_orders",
        "depot_basis": "all_bling_depots_aggregate",
    }
    with erp.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            if db.execute("SELECT 1 FROM products LIMIT 1").fetchone():
                raise ERPError(
                    "empty_workspace_required", "A carga inicial exige uma base local nova.", 409
                )
            key = "real-catalog-opening-v1"
            db.execute(
                "INSERT INTO events(operation_key,fingerprint,kind,actor,payload,created_at) "
                "VALUES (?,?,?,?,?,?)",
                (
                    key,
                    metadata["digest"],
                    "catalog.bootstrap",
                    "carga inicial local",
                    dumps({"source_digest": metadata["digest"]}),
                    erp.now(),
                ),
            )
            db.execute("INSERT INTO deposits VALUES (?,?)", ("geral", "Saldo agregado de origem"))
            db.execute("INSERT INTO deposits VALUES (?,?)", ("retornos", "Retornos locais"))
            for sku, p in sorted(selected.items()):
                kit = p["formato"] == "E"
                parts = (
                    [
                        {"sku": s, "quantity": str(q)}
                        for s, q in sorted(resolved.get(sku, {}).items())
                    ]
                    if kit
                    else []
                )
                state = "ready" if sku not in invalid and p["formato"] in {"S", "E"} else "review"
                stock_state = "derived" if kit else "missing"
                stock_basis = "derived" if kit else "missing"
                physical = reserved = None
                if not kit:
                    try:
                        fresh = live_balances.get(p["bling_product_id"])
                        physical = Decimal(str(fresh["physical"] if fresh else p["saldo_fisico"]))
                        available = Decimal(
                            str(fresh["available"] if fresh else p["saldo_virtual_total"])
                        )
                        reserved = physical - available
                        if (
                            not all(n.is_finite() for n in [physical, available, reserved])
                            or reserved < 0
                        ):
                            raise InvalidOperation
                        stock_state = "known"
                        stock_basis = "bling_api_initial" if fresh else "mirror_initial"
                        report["direct_stock" if fresh else "mirror_stock"] += 1
                    except (InvalidOperation, TypeError):
                        physical = reserved = None
                    report["known_stock" if stock_state == "known" else "unknown_stock"] += 1
                origin = {
                    "source_id": p["id"],
                    "bling_product_id": p["bling_product_id"],
                    "sellable": p["situacao"] == "A",
                    "catalog_state": state,
                    "stock_state": stock_state,
                    "stock_basis": stock_basis,
                    "source_status": p["situacao"],
                    "source_updated_at": p["updated_at"],
                }
                db.execute(
                    "INSERT INTO products VALUES (?,?,?,?)",
                    (sku, p["name"], "kit" if kit else "simple", dumps(parts)),
                )
                db.execute("INSERT INTO catalog_origin VALUES (?,?)", (sku, dumps(origin)))
                db.execute(
                    "INSERT INTO product_pricing VALUES (?,?,?)",
                    (
                        sku,
                        p["price"],
                        p["bling_cost_price"]
                        if p["bling_cost_price"] is not None
                        else p["cost_price"],
                    ),
                )
                if physical is not None:
                    if physical - reserved < 0:
                        report["shortages"] += 1
                    db.execute(
                        "INSERT INTO balances VALUES (?,?,?,?)",
                        (sku, "geral", str(physical), str(reserved)),
                    )
                    db.execute(
                        "INSERT INTO movements(operation_key,sku,depot,physical_delta,"
                        "reserved_delta,"
                        "reason,created_at) VALUES (?,?,?,?,?,?,?)",
                        (
                            key,
                            sku,
                            "geral",
                            str(physical),
                            str(reserved),
                            "saldo e reserva de abertura",
                            erp.now(),
                        ),
                    )
            db.execute("INSERT INTO catalog_import VALUES (1,?)", (dumps(report),))
            db.execute("UPDATE events SET result=? WHERE operation_key=?", (dumps(report), key))
            db.commit()
        except Exception:
            db.rollback()
            raise
    return report
