"""Sanitized native observations; intentionally separate from active own sales.

Reading historical marketplace orders must not reserve a second copy of stock
still controlled by Bling. Cutover and reconciliation remain explicit steps.
"""

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.erp_local import amazon_order_probe as probe
from app.erp_local import shopee_order_probe as shopee
from app.erp_local.core import ERPError, dumps

SCHEMA = """
CREATE TABLE IF NOT EXISTS native_order_reads (
 digest TEXT PRIMARY KEY, source TEXT NOT NULL, captured_at TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS native_order_observations (
 id TEXT PRIMARY KEY, read_digest TEXT REFERENCES native_order_reads(digest),
 channel TEXT NOT NULL, store TEXT NOT NULL, external_id TEXT NOT NULL,
 updated_at TEXT NOT NULL, captured_at TEXT NOT NULL, data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS native_order_identity
 ON native_order_observations(channel,store,external_id,updated_at);
CREATE INDEX IF NOT EXISTS stock_routes_identity
 ON stock_routes(channel,store,external_id,variation_id,active);
"""
COMMANDS = {"native.orders.import"}
STATES = {
    "read_ok",
    "auth_denied",
    "rate_limited",
    "provider_unavailable",
    "invalid_response",
    "credentials_missing",
    "configuration_missing",
    "credential_unreadable",
    "marketplace_mismatch",
}


def validate_money(value):
    if value is not None:
        if not isinstance(value, dict) or set(value) != {"amount", "currency"}:
            raise ValueError
        probe.money({"amount": value["amount"], "currencyCode": value["currency"]})


def validate(read):
    if isinstance(read, dict) and read.get("source") == "ml_orders_v1_read":
        from app.erp_local.ml_orders import validate as validate_ml

        return validate_ml(read)
    if isinstance(read, dict) and read.get("source") == "magalu_orders_v1_read":
        from app.erp_local.magalu_orders import validate as validate_magalu

        return validate_magalu(read)
    if isinstance(read, dict) and read.get("source") == "carrefour_orders_or11_read":
        from app.erp_local.carrefour_orders import validate as validate_carrefour

        return validate_carrefour(read)
    if isinstance(read, dict) and read.get("source") == "tiktok_orders_202309_read":
        from app.erp_local.tiktok_orders import validate as validate_tiktok

        return validate_tiktok(read)
    try:
        platform = {"amazon_orders_2026_read": "amazon", "shopee_orders_v2_read": "shopee"}.get(
            read.get("source") if isinstance(read, dict) else None
        )
        is_shopee = platform == "shopee"
        valid_versions = {1, 2} if is_shopee else {1, 2, 3, 4}
        if (
            not isinstance(read, dict)
            or set(read)
            != {
                "version",
                "source",
                "read_only",
                "no_token_refresh",
                "external_business_writes",
                "captured_at",
                "limit_per_account",
                "accounts",
            }
            or type(read["version"]) is not int
            or read["version"] not in valid_versions
            or platform is None
            or read["read_only"] != "on"
            or read["no_token_refresh"] is not True
            or read["external_business_writes"] is not False
            or type(read["limit_per_account"]) is not int
            or read["limit_per_account"]
            != (
                shopee.LIMIT
                if is_shopee and read["version"] == 2
                else 2
                if not is_shopee and read["version"] == 1
                else 100
            )
        ):
            raise ValueError
        captured = probe.stamp(read["captured_at"])
        if captured > datetime.now(UTC) + timedelta(minutes=5):
            raise ValueError
        if not isinstance(read["accounts"], list) or not 1 <= len(read["accounts"]) <= (
            16 if is_shopee else 4
        ):
            raise ValueError
        ids = set()
        for account in read["accounts"]:
            account_fields = {
                "integration_id",
                "platform",
                "credentials_revision",
                "checked_at",
                "date_from",
                "date_to",
                "state",
                "http_status",
                "orders",
                "has_more",
            }
            if read["version"] >= 3 or is_shopee:
                account_fields.add("failure_stage")
            if is_shopee:
                account_fields.add("missing_details")
                if read["version"] == 2:
                    account_fields.add("pages_read")
            if not isinstance(account, dict) or set(account) != account_fields:
                raise ValueError
            if account.get("failure_stage") not in {
                None,
                "dates",
                "identifiers",
                "money",
                "fulfillment",
                "items",
                "quantity",
                "shape",
            }:
                raise ValueError
            identifier = str(UUID(account["integration_id"]))
            if identifier != account["integration_id"] or identifier in ids:
                raise ValueError
            ids.add(identifier)
            valid_states = STATES | {"partial_read", "not_run"} if is_shopee else STATES
            if account["platform"] != platform or account["state"] not in valid_states:
                raise ValueError
            if is_shopee and (
                type(account["missing_details"]) is not int
                or not 0 <= account["missing_details"] <= read["limit_per_account"]
                or (account["state"] == "partial_read") != (account["missing_details"] > 0)
            ):
                raise ValueError
            if (
                is_shopee
                and read["version"] == 2
                and (
                    type(account["pages_read"]) is not int
                    or not (1 if account["state"] in {"read_ok", "partial_read"} else 0)
                    <= account["pages_read"]
                    <= (shopee.MAX_PAGES if account["state"] in {"read_ok", "partial_read"} else 0)
                    or len(account["orders"]) + account["missing_details"]
                    > account["pages_read"] * shopee.PAGE_SIZE
                )
            ):
                raise ValueError
            checked = probe.stamp(account["checked_at"])
            if probe.stamp(account["credentials_revision"]) > checked or checked > captured:
                raise ValueError
            status = account["http_status"]
            if status is not None and (type(status) is not int or not 100 <= status <= 599):
                raise ValueError
            if (
                not isinstance(account["orders"], list)
                or len(account["orders"]) > read["limit_per_account"]
            ):
                raise ValueError
            if account["date_from"] is not None or account["date_to"] is not None:
                start, end = probe.stamp(account["date_from"]), probe.stamp(account["date_to"])
                if not 0 < (end - start).total_seconds() <= 86400 or end > checked:
                    raise ValueError
            elif account["state"] in {"read_ok", "partial_read"}:
                raise ValueError
            if account["state"] in {"read_ok", "partial_read"}:
                if (
                    status != 200
                    or type(account["has_more"]) is not bool
                    or account.get("failure_stage") is not None
                ):
                    raise ValueError
            elif account["orders"] or account["has_more"] is not None:
                raise ValueError
            seen, shop_ids = set(), set()
            for order in account["orders"]:
                order_fields = {
                    "id",
                    "created_at",
                    "updated_at",
                    "status",
                    "fulfilled_by",
                    "total",
                    "items",
                    "within_window",
                } | {"shop_id" if is_shopee else "marketplace_id"}
                if not isinstance(order, dict) or set(order) != order_fields:
                    raise ValueError
                pattern = r"[A-Za-z0-9-]{1,64}" if is_shopee else r"\d{3}-\d{7}-\d{7}"
                if not re.fullmatch(pattern, order["id"]) or order["id"] in seen:
                    raise ValueError
                seen.add(order["id"])
                if is_shopee:
                    if shopee.identifier(order["shop_id"]) != order["shop_id"]:
                        raise ValueError
                    shop_ids.add(order["shop_id"])
                    if len(shop_ids) > 1:
                        raise ValueError
                updated = probe.stamp(order["updated_at"])
                status_ok = (
                    (
                        isinstance(order["status"], str)
                        and re.fullmatch(r"[A-Z_]{1,64}", order["status"])
                    )
                    if is_shopee
                    else order["status"] in probe.STATUSES
                )
                if (
                    probe.stamp(order["created_at"]) > updated
                    or not status_ok
                    or order["fulfilled_by"]
                    not in {"SHOPEE" if is_shopee else "AMAZON", "MERCHANT", None}
                    or not is_shopee
                    and order["marketplace_id"] != probe.MARKETPLACE
                    or type(order["within_window"]) is not bool
                    or order["within_window"] != (start <= updated <= end)
                ):
                    raise ValueError
                validate_money(order["total"])
                if not isinstance(order["items"], list) or not 1 <= len(order["items"]) <= 200:
                    raise ValueError
                lines = set()
                for index, item in enumerate(order["items"]):
                    item_fields = {
                        "line_id",
                        "seller_sku",
                        "quantity",
                        "unit_price",
                    } | (
                        {"listing_id", "variation_id"}
                        if is_shopee
                        else {"asin", "proceeds_total", "measurement_review"}
                    )
                    if not isinstance(item, dict) or set(item) != item_fields:
                        raise ValueError
                    probe.string(item["line_id"], limit=120)
                    probe.string(item["seller_sku"], optional=True)
                    if is_shopee:
                        listing, variation = (
                            shopee.identifier(item["listing_id"]),
                            shopee.identifier(item["variation_id"], zero=True),
                        )
                        if (
                            item["listing_id"] != listing
                            or item["variation_id"] != variation
                            or item["line_id"] != f"{listing}:{variation}:{index}"
                        ):
                            raise ValueError
                    else:
                        probe.string(item["asin"], limit=20, optional=True)
                    if (
                        item["line_id"] in lines
                        or type(item["quantity"]) is not int
                        or not (0 if is_shopee or read["version"] >= 4 else 1)
                        <= item["quantity"]
                        <= 1000000
                        or not is_shopee
                        and type(item["measurement_review"]) is not bool
                    ):
                        raise ValueError
                    lines.add(item["line_id"])
                    validate_money(item["unit_price"])
                    if not is_shopee:
                        validate_money(item["proceeds_total"])
    except (ValueError, KeyError, TypeError, AttributeError, UnboundLocalError):
        raise ERPError(
            "native_read_invalid", "Leitura nativa fora da projeção permitida."
        ) from None


def apply(erp, db, payload):
    if not isinstance(payload, dict) or set(payload) != {"read"}:
        raise ERPError("native_read_invalid", "Informe somente a leitura nativa projetada.")
    read = payload["read"]
    validate(read)
    for account in read["accounts"]:
        if not db.execute(
            "SELECT 1 FROM store_policies WHERE channel=? AND store=? "
            "AND status IN ('shadow','identity_review')",
            (account["platform"], account["integration_id"]),
        ).fetchone():
            raise ERPError(
                "native_store_review", "A conta precisa constar no registro próprio de lojas.", 409
            )
    digest = hashlib.sha256(dumps(read).encode()).hexdigest()
    db.execute(
        "INSERT OR IGNORE INTO native_order_reads VALUES (?,?,?,?)",
        (digest, read["source"], read["captured_at"], dumps(read)),
    )
    inserted = 0
    for account in read["accounts"]:
        for order in account["orders"]:
            identifier = hashlib.sha256(
                dumps([account["integration_id"], order]).encode()
            ).hexdigest()
            inserted += db.execute(
                "INSERT OR IGNORE INTO native_order_observations VALUES (?,?,?,?,?,?,?,?)",
                (
                    identifier,
                    digest,
                    account["platform"],
                    account["integration_id"],
                    order["id"],
                    order["updated_at"],
                    read["captured_at"],
                    dumps(order),
                ),
            ).rowcount
    return {
        "digest": digest,
        "observations_added": inserted,
        "accounts_checked": len(read["accounts"]),
        "active_orders_created": 0,
        "external_writes": False,
        "stock_reserved": False,
    }


def import_read(erp, read):
    validate(read)
    digest = hashlib.sha256(dumps(read).encode()).hexdigest()
    return erp.command(
        "native-orders-" + digest,
        "native.orders.import",
        {"read": read},
        actor="leitura nativa em conferência",
    )


def snapshot(db):
    from app.erp_local import native_reconciliation

    source = native_reconciliation.context(db)
    reads, heads = {}, {}
    for row in db.execute("SELECT * FROM native_order_reads ORDER BY captured_at DESC"):
        report = json.loads(row["data"])
        for account in report["accounts"]:
            reads.setdefault(
                account["integration_id"],
                {
                    **account,
                    "limit_per_account": report["limit_per_account"],
                    "read_captured_at": row["captured_at"],
                    "outside_window_count": sum(
                        o["within_window"] is False for o in account["orders"]
                    ),
                },
            )
    for row in db.execute(
        "SELECT * FROM native_order_observations ORDER BY updated_at DESC,captured_at DESC"
    ):
        identity = (row["channel"], row["store"], row["external_id"])
        data = json.loads(row["data"])
        if identity not in heads:
            heads[identity] = {
                **dict(row),
                "data": data,
                "revision_conflict": False,
                "local_items": [
                    resolve_item(db, row["store"], item, channel=row["channel"])
                    for item in data["items"]
                ],
            }
        elif row["updated_at"] == heads[identity]["updated_at"] and data != heads[identity]["data"]:
            heads[identity]["revision_conflict"] = True
    for head in heads.values():
        head["source_reconciliation"] = native_reconciliation.compare(
            head["channel"], head["store"], head["data"], head["local_items"], source
        )
    return {
        "accounts": list(reads.values()),
        "orders": list(heads.values()),
        "active_processing": False,
        "external_writes": False,
    }


def resolve_item(db, store, item, *, channel="amazon"):
    """Amazon: seller SKU + ASIN. Shopee: listing/model ID (including model 0).

    TikTok: product ID + SKU ID, separately scoped to the exact account.

    A matching catalogue name/SKU alone does not establish the account listing.
    """
    result = {"line_id": item["line_id"], "state": "missing", "sku": None, "kind": None}
    external, variation = (
        (item["listing_id"], item["variation_id"])
        if channel in {"shopee", "tiktok", "carrefour", "magalu", "ml"}
        else (item["seller_sku"], item["asin"])
    )
    if not external or channel not in {"carrefour", "magalu", "ml"} and variation in (None, ""):
        return result
    matches = db.execute(
        "SELECT DISTINCT p.sku,p.kind FROM stock_routes r "
        "JOIN products p ON p.sku=r.sku WHERE r.channel=? "
        "AND r.store=? AND r.external_id=? AND r.variation_id IS ? AND r.active=1",
        (channel, store, external, variation),
    ).fetchall()
    if len(matches) != 1:
        return {**result, "state": "ambiguous" if matches else "missing"}
    matched = matches[0]
    origin = db.execute("SELECT data FROM catalog_origin WHERE sku=?", (matched["sku"],)).fetchone()
    return {
        **result,
        "sku": matched["sku"],
        "kind": matched["kind"],
        "state": "catalog_review"
        if origin and json.loads(origin[0])["catalog_state"] != "ready"
        else "matched",
    }
