"""Strict, PII-free contract for a bounded, unfiltered Magalu Orders sample."""

import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from app.erp_local import magalu_order_probe as probe
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
    "coverage_scope",
    "identity_match",
    "date_from",
    "date_to",
    "failure_stage",
}
ORDER_FIELDS = {
    "id",
    "platform_id",
    "created_at",
    "updated_at",
    "status",
    "fulfilled_by",
    "total",
    "items",
    "within_window",
}
ITEM_FIELDS = {
    "line_id",
    "delivery_id",
    "delivery_status",
    "product_id",
    "listing_id",
    "variation_id",
    "seller_sku",
    "quantity",
    "unit_price",
    "total",
}
STATES = {
    "read_ok",
    "auth_denied",
    "rate_limited",
    "provider_unavailable",
    "invalid_response",
    "credentials_missing",
    "configuration_missing",
    "credential_unreadable",
}


def money(raw):
    if raw is None:
        return
    if (
        not isinstance(raw, dict)
        or set(raw) != {"amount", "currency"}
        or not isinstance(raw["amount"], str)
        or not re.fullmatch(r"\d{1,9}(?:\.\d{1,6})?", raw["amount"])
        or Decimal(raw["amount"]) > Decimal("999999999")
        or not isinstance(raw["currency"], str)
        or not re.fullmatch(r"[A-Z]{3}", raw["currency"])
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
            or read["source"] != "magalu_orders_v1_read"
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
            or not 1 <= len(read["accounts"]) <= 3
        ):
            raise ValueError
        seen_accounts = set()
        for account in read["accounts"]:
            if (
                not isinstance(account, dict)
                or set(account) != ACCOUNT_FIELDS
                or account["platform"] != "magalu"
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
                account["coverage_scope"] != "bounded_unfiltered"
                or any(account[k] is not None for k in ["identity_match", "date_from", "date_to"])
                or account["failure_stage"]
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
                }
                or not isinstance(account["orders"], list)
                or len(account["orders"]) > probe.LIMIT
                or type(account["pages_read"]) is not int
            ):
                raise ValueError
            if account["state"] != "read_ok":
                if account["orders"] or account["has_more"] is not None or account["pages_read"]:
                    raise ValueError
                continue
            if (
                code != 200
                or account["failure_stage"] is not None
                or type(account["has_more"]) is not bool
                or not 1 <= account["pages_read"] <= probe.MAX_PAGES
                or len(account["orders"]) > account["pages_read"] * probe.PAGE_SIZE
                or account["has_more"]
                and account["pages_read"] != probe.MAX_PAGES
            ):
                raise ValueError
            seen_orders, seen_platform = set(), set()
            for order in account["orders"]:
                if (
                    not isinstance(order, dict)
                    or set(order) != ORDER_FIELDS
                    or probe.string(order["id"], 120) != order["id"]
                    or probe.identifier(order["platform_id"]) != order["platform_id"]
                    or order["id"] in seen_orders
                    or order["platform_id"] in seen_platform
                    or order["fulfilled_by"] is not None
                    or order["within_window"] is not None
                    or probe.stamp(order["created_at"]) > probe.stamp(order["updated_at"])
                    or not isinstance(order["items"], list)
                    or not 1 <= len(order["items"]) <= 200
                ):
                    raise ValueError
                seen_orders.add(order["id"])
                seen_platform.add(order["platform_id"])
                probe.status(order["status"])
                money(order["total"])
                seen_lines = set()
                for item in order["items"]:
                    if (
                        not isinstance(item, dict)
                        or set(item) != ITEM_FIELDS
                        or probe.identifier(item["delivery_id"]) != item["delivery_id"]
                        or probe.identifier(item["product_id"]) != item["product_id"]
                        or not isinstance(item["line_id"], str)
                        or not re.fullmatch(
                            re.escape(item["delivery_id"]) + r":\d{1,3}", item["line_id"]
                        )
                        or item["line_id"] in seen_lines
                        or probe.string(item["seller_sku"]) != item["seller_sku"]
                        or item["listing_id"] != item["seller_sku"]
                        or item["variation_id"] is not None
                        or type(item["quantity"]) is not int
                        or not 0 <= item["quantity"] <= 1000000
                    ):
                        raise ValueError
                    seen_lines.add(item["line_id"])
                    probe.status(item["delivery_status"])
                    money(item["unit_price"])
                    money(item["total"])
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        raise ERPError("native_read_invalid", "A leitura de pedidos exige conferência.") from None
    return read
