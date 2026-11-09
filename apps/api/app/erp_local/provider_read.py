"""Import sanitized provider observations; ambiguous/old orders stay in review."""

import hashlib

from app.erp_local.channels import ChannelFlow, timestamp
from app.erp_local.core import ERPError, dumps, number


def import_ml_read(erp, read):
    fields = {
        "source",
        "integration_id",
        "started_at",
        "captured_at",
        "date_from",
        "date_to",
        "db_read_only",
        "no_token_refresh",
        "orders",
        "total_eligible",
        "limit",
        "calls",
        "failures",
    }
    if (
        not isinstance(read, dict)
        or set(read) != fields
        or read["source"] != "ml_api_read"
        or read["db_read_only"] != "on"
        or read["no_token_refresh"] is not True
    ):
        raise ERPError("invalid_provider_read", "Projeção da leitura direta inválida.")
    for field in ("started_at", "captured_at", "date_from", "date_to"):
        timestamp(read[field])
    if not isinstance(read["orders"], list) or len(read["orders"]) > 20:
        raise ERPError("invalid_provider_read", "Volume da leitura direta inválido.")
    for call in read["calls"]:
        if set(call) != {"method", "path", "status"} or call["method"] != "GET":
            raise ERPError("invalid_provider_read", "A leitura contém ação não permitida.")
    for failure in read["failures"]:
        if set(failure) not in ({"code"}, {"path", "status"}):
            raise ERPError("invalid_provider_read", "Erro fora da projeção permitida.")
    # Validate the entire projection before writing the first receipt.
    for order in read["orders"]:
        if set(order) != {
            "id",
            "status",
            "created_at",
            "updated_at",
            "currency",
            "shipment",
            "items",
        } or set(order["shipment"]) != {"status", "logistic_type"}:
            raise ERPError("invalid_provider_read", "Pedido fora da projeção sem dados pessoais.")
        timestamp(order["created_at"])
        timestamp(order["updated_at"])
        if not isinstance(order["items"], list) or not 1 <= len(order["items"]) <= 200:
            raise ERPError("invalid_provider_read", "Itens da leitura inválidos.")
        for item in order["items"]:
            if set(item) != {"listing_id", "variation_id", "quantity", "unit_price"}:
                raise ERPError("invalid_provider_read", "Item fora da projeção permitida.")
            number(item["quantity"])
            number(item["unit_price"], unit=True, zero=True)
    digest = hashlib.sha256(dumps(read).encode()).hexdigest()
    flow = ChannelFlow(erp)
    results = []
    for order in read["orders"]:
        code = None
        if (
            not timestamp(read["date_from"])
            <= timestamp(order["updated_at"])
            <= timestamp(read["date_to"])
        ):
            code = "outside_requested_window"
        elif order["currency"] != "BRL":
            code = "currency_review"
        elif order["status"] not in {"paid", "cancelled"}:
            code = "status_mapping_review"
        elif order["status"] == "paid" and (
            order["shipment"]["status"] not in {"pending", "handling", "ready_to_ship"}
            or order["shipment"]["logistic_type"] == "fulfillment"
        ):
            code = "dispatch_reconciliation"
        items = []
        if not code and order["status"] == "paid":
            with erp.connection() as db:
                for item in order["items"]:
                    matches = db.execute(
                        "SELECT DISTINCT sku FROM stock_routes WHERE active=1 AND channel='ml' "
                        "AND store=? AND external_id=? AND coalesce(variation_id,'')=?",
                        (read["integration_id"], item["listing_id"], item["variation_id"] or ""),
                    ).fetchall()
                    if len(matches) != 1:
                        code = "listing_identity_review"
                        break
                    items.append(
                        {
                            "sku": matches[0][0],
                            "quantity": item["quantity"],
                            "unit_price": item["unit_price"],
                        }
                    )
        result = {"order_id": order["id"], "status": "review", "code": code}
        if not code:
            kind = "order.confirmed" if order["status"] == "paid" else "order.cancelled"
            receipt = flow.receive(
                {
                    "channel": "ml",
                    "store": read["integration_id"],
                    "event_id": "ml-" + order["id"] + "-" + order["updated_at"] + "-" + kind,
                    "external_id": order["id"],
                    "event_type": kind,
                    "order_created_at": order["created_at"],
                    "occurred_at": order["updated_at"],
                    "depot": "geral",
                    "items": items,
                },
                source="provider_read",
            )
            result.update(
                status=receipt["status"],
                inbox_id=receipt["id"],
                code=(receipt["outcome"] or {}).get("code"),
            )
        results.append(result)
    report = {
        "integration_id": read["integration_id"],
        "captured_at": read["captured_at"],
        "source": "ml_direct",
        "external_writes": False,
        "orders_observed": len(read["orders"]),
        "total_eligible": read["total_eligible"],
        "limit": read["limit"],
        "calls": read["calls"],
        "failures": read["failures"],
        "results": results,
        "read_digest": digest,
    }
    # Read evidence is retained independently of the normalized command receipts.
    with erp.connection() as db:
        db.execute(
            "INSERT OR IGNORE INTO events(operation_key,fingerprint,kind,actor,payload,result,"
            "created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                "ml-read-" + digest,
                digest,
                "channels.provider.read",
                "leitura direta",
                dumps(read),
                dumps(report),
                erp.now(),
            ),
        )
    return report
