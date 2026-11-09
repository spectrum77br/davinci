"""Strict native ML order projection: exact seller, listing and variation IDs."""

import re
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.erp_local import ml_order_probe as probe
from app.erp_local.core import ERPError

ACCOUNT_FIELDS = {
    "integration_id",
    "platform",
    "credentials_revision",
    "checked_at",
    "state",
    "http_status",
    "orders",
    "has_more",
    "pages_read",
    "total_eligible",
    "coverage_review",
    "identity_match",
    "seller_id",
    "date_from",
    "date_to",
    "failure_stage",
}
ORDER_FIELDS = {
    "id",
    "seller_id",
    "pack_id",
    "shipping_id",
    "created_at",
    "updated_at",
    "status",
    "fulfilled_by",
    "total",
    "paid",
    "coupon",
    "items",
    "within_window",
}
ITEM_FIELDS = {
    "line_id",
    "listing_id",
    "variation_id",
    "seller_sku",
    "seller_custom_field",
    "quantity",
    "unit_price",
}
STATES = {
    "read_ok",
    "partial_read",
    "not_run",
    "auth_denied",
    "rate_limited",
    "provider_unavailable",
    "invalid_response",
    "credentials_missing",
    "configuration_missing",
    "credential_unreadable",
    "identity_mismatch",
}


def money(value):
    if value is not None:
        if (
            not isinstance(value, dict)
            or set(value) != {"amount", "currency"}
            or not isinstance(value["amount"], str)
            or probe.monetary(value["amount"], value["currency"]) != value
        ):
            raise ValueError


def validate(read):
    try:
        if (
            not isinstance(read, dict)
            or set(read)
            != {
                "version",
                "source",
                "read_only",
                "no_token_refresh",
                "external_business_writes",
                "limit_per_account",
                "captured_at",
                "accounts",
            }
            or type(read["version"]) is not int
            or read["version"] != 1
            or read["source"] != "ml_orders_v1_read"
            or read["read_only"] != "on"
            or read["no_token_refresh"] is not True
            or read["external_business_writes"] is not False
            or type(read["limit_per_account"]) is not int
            or read["limit_per_account"] != probe.LIMIT
        ):
            raise ValueError
        captured = probe.stamp(read["captured_at"])
        if (
            captured > datetime.now(UTC) + timedelta(minutes=5)
            or not isinstance(read["accounts"], list)
            or not 1 <= len(read["accounts"]) <= 24
        ):
            raise ValueError
        seen_accounts = set()
        for account in read["accounts"]:
            if (
                not isinstance(account, dict)
                or set(account) != ACCOUNT_FIELDS
                or account["platform"] != "ml"
                or account["state"] not in STATES
            ):
                raise ValueError
            aid = str(UUID(account["integration_id"]))
            if aid != account["integration_id"] or aid in seen_accounts:
                raise ValueError
            seen_accounts.add(aid)
            checked = probe.stamp(account["checked_at"])
            if probe.stamp(account["credentials_revision"]) > checked or checked > captured:
                raise ValueError
            code = account["http_status"]
            if code is not None and (type(code) is not int or not 100 <= code <= 599):
                raise ValueError
            if (
                account["failure_stage"]
                not in {
                    None,
                    "dates",
                    "identifiers",
                    "money",
                    "fulfillment",
                    "items",
                    "quantity",
                    "shape",
                    "pagination",
                    "seller",
                }
                or not isinstance(account["orders"], list)
                or len(account["orders"]) > probe.LIMIT
                or type(account["pages_read"]) is not int
                or type(account["coverage_review"]) is not bool
            ):
                raise ValueError
            if account["state"] not in {"read_ok", "partial_read"}:
                if (
                    account["orders"]
                    or account["has_more"] is not None
                    or account["pages_read"]
                    or account["coverage_review"]
                    or any(
                        account[k] is not None
                        for k in [
                            "identity_match",
                            "seller_id",
                            "total_eligible",
                            "date_from",
                            "date_to",
                        ]
                    )
                ):
                    raise ValueError
                continue
            start, end = probe.stamp(account["date_from"]), probe.stamp(account["date_to"])
            if (
                code != 200
                or account["failure_stage"] is not None
                or account["identity_match"] is not True
                or probe.identifier(account["seller_id"]) != account["seller_id"]
                or type(account["has_more"]) is not bool
                or not 1 <= account["pages_read"] <= probe.MAX_PAGES
                or len(account["orders"]) > account["pages_read"] * probe.PAGE_SIZE
                or not 0 < (end - start).total_seconds() <= 86400
                or end > checked
                or type(account["total_eligible"]) is not int
                or not 0 <= account["total_eligible"] <= 10000000
                or (account["state"] == "partial_read") != account["coverage_review"]
                or not account["has_more"]
                and account["total_eligible"] != len(account["orders"])
                and not account["coverage_review"]
            ):
                raise ValueError
            seen_orders = set()
            for order in account["orders"]:
                if (
                    not isinstance(order, dict)
                    or set(order) != ORDER_FIELDS
                    or probe.identifier(order["id"]) != order["id"]
                    or order["id"] in seen_orders
                    or order["seller_id"] != account["seller_id"]
                    or probe.identifier(order["pack_id"], optional=True) != order["pack_id"]
                    or probe.identifier(order["shipping_id"], optional=True) != order["shipping_id"]
                    or order["fulfilled_by"] is not None
                    or type(order["within_window"]) is not bool
                    or probe.stamp(order["created_at"]) > probe.stamp(order["updated_at"])
                    or order["within_window"] != (start <= probe.stamp(order["updated_at"]) <= end)
                    or not isinstance(order["items"], list)
                    or not 1 <= len(order["items"]) <= 200
                ):
                    raise ValueError
                seen_orders.add(order["id"])
                probe.status(order["status"])
                money(order["total"])
                if order["paid"] is not None:
                    money(order["paid"])
                money(order["coupon"])
                for index, item in enumerate(order["items"]):
                    if (
                        not isinstance(item, dict)
                        or set(item) != ITEM_FIELDS
                        or item["line_id"] != str(index)
                        or not isinstance(item["listing_id"], str)
                        or not re.fullmatch(r"MLB[1-9]\d{0,19}", item["listing_id"])
                        or probe.identifier(item["variation_id"], optional=True)
                        != item["variation_id"]
                        or probe.string(item["seller_sku"], optional=True) != item["seller_sku"]
                        or probe.string(item["seller_custom_field"], optional=True)
                        != item["seller_custom_field"]
                        or type(item["quantity"]) is not int
                        or not 0 <= item["quantity"] <= 1000000
                    ):
                        raise ValueError
                    money(item["unit_price"])
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        raise ERPError("native_read_invalid", "A leitura de pedidos exige conferência.") from None
    return read
