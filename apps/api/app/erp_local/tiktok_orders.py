"""Strict import contract for TikTok observations, with no inferred quantities."""

import re
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.erp_local import amazon_order_probe as common
from app.erp_local import tiktok_order_probe as probe
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
    "shop_id",
    "date_from",
    "date_to",
    "failure_stage",
}
ORDER_FIELDS = {
    "id",
    "shop_id",
    "created_at",
    "updated_at",
    "status",
    "fulfilled_by",
    "total",
    "shipping_fee",
    "items",
    "within_window",
}
ITEM_FIELDS = {
    "line_id",
    "listing_id",
    "variation_id",
    "seller_sku",
    "quantity",
    "quantity_review",
    "sale_price",
    "line_status",
    "composition_review",
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


def money(raw):
    if raw is not None:
        if (
            not isinstance(raw, dict)
            or set(raw) != {"amount", "currency"}
            or not isinstance(raw["amount"], str)
        ):
            raise ValueError
        if probe.monetary(raw["amount"], raw["currency"]) != raw:
            raise ValueError


def status(value, *, optional=False):
    if optional and value is None:
        return
    if not isinstance(value, str) or not re.fullmatch(r"[A-Z_]{1,64}", value):
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
            or read["source"] != "tiktok_orders_202309_read"
            or read["read_only"] != "on"
            or read["no_token_refresh"] is not True
            or read["external_business_writes"] is not False
            or type(read["limit_per_account"]) is not int
            or read["limit_per_account"] != probe.LIMIT
        ):
            raise ValueError
        captured = common.stamp(read["captured_at"])
        if (
            captured > datetime.now(UTC) + timedelta(minutes=5)
            or not isinstance(read["accounts"], list)
            or not 1 <= len(read["accounts"]) <= 8
        ):
            raise ValueError
        identifiers = set()
        for account in read["accounts"]:
            if (
                not isinstance(account, dict)
                or set(account) != ACCOUNT_FIELDS
                or account["platform"] != "tiktok"
                or account["state"] not in STATES
            ):
                raise ValueError
            aid = str(UUID(account["integration_id"]))
            if aid != account["integration_id"] or aid in identifiers:
                raise ValueError
            identifiers.add(aid)
            checked = common.stamp(account["checked_at"])
            if common.stamp(account["credentials_revision"]) > checked or checked > captured:
                raise ValueError
            if account["failure_stage"] not in {
                None,
                "dates",
                "identifiers",
                "money",
                "fulfillment",
                "items",
                "quantity",
                "shape",
                "api_envelope",
                "api_code",
                "authorization_shape",
                "order_list",
                "order_count",
                "empty_list_omitted",
            }:
                raise ValueError
            http = account["http_status"]
            if http is not None and (type(http) is not int or not 100 <= http <= 599):
                raise ValueError
            if (
                not isinstance(account["orders"], list)
                or len(account["orders"]) > probe.LIMIT
                or type(account["coverage_review"]) is not bool
                or type(account["pages_read"]) is not int
            ):
                raise ValueError
            if account["state"] not in {"read_ok", "partial_read"}:
                if (
                    account["orders"]
                    or account["has_more"] is not None
                    or account["pages_read"] != 0
                    or any(
                        account[k] is not None
                        for k in [
                            "shop_id",
                            "identity_match",
                            "total_eligible",
                            "date_from",
                            "date_to",
                        ]
                    )
                    or account["coverage_review"]
                ):
                    raise ValueError
                continue
            start, end = common.stamp(account["date_from"]), common.stamp(account["date_to"])
            if (
                http != 200
                or account["failure_stage"] is not None
                or type(account["has_more"]) is not bool
                or account["identity_match"] is not True
                or probe.identifier(account["shop_id"]) != account["shop_id"]
                or not 0 < (end - start).total_seconds() <= 86400
                or end > checked
                or not 1 <= account["pages_read"] <= probe.MAX_PAGES
                or len(account["orders"]) > account["pages_read"] * probe.PAGE_SIZE
                or type(account["total_eligible"]) is not int
                or not 0 <= account["total_eligible"] <= 10000000
                or (account["state"] == "partial_read") != account["coverage_review"]
            ):
                raise ValueError
            if (
                not account["has_more"]
                and account["total_eligible"] != len(account["orders"])
                and not account["coverage_review"]
            ):
                raise ValueError
            seen = set()
            for order in account["orders"]:
                if (
                    not isinstance(order, dict)
                    or set(order) != ORDER_FIELDS
                    or probe.identifier(order["id"]) != order["id"]
                    or order["id"] in seen
                    or order["shop_id"] != account["shop_id"]
                ):
                    raise ValueError
                seen.add(order["id"])
                status(order["status"])
                updated = common.stamp(order["updated_at"])
                if (
                    common.stamp(order["created_at"]) > updated
                    or order["fulfilled_by"] not in {None, "MERCHANT", "TIKTOK"}
                    or type(order["within_window"]) is not bool
                    or order["within_window"] != (start <= updated < end)
                    or not isinstance(order["items"], list)
                    or len(order["items"]) > 200
                ):
                    raise ValueError
                money(order["total"])
                money(order["shipping_fee"])
                lines = set()
                for item in order["items"]:
                    if not isinstance(item, dict) or set(item) != ITEM_FIELDS:
                        raise ValueError
                    for key in ["line_id", "listing_id", "variation_id"]:
                        if probe.identifier(item[key]) != item[key]:
                            raise ValueError
                    if item["line_id"] in lines:
                        raise ValueError
                    lines.add(item["line_id"])
                    common.string(item["seller_sku"], optional=True)
                    if (
                        type(item["quantity_review"]) is not bool
                        or type(item["composition_review"]) is not bool
                        or item["quantity_review"] != (item["quantity"] is None)
                        or item["quantity"] is not None
                        and (
                            type(item["quantity"]) is not int
                            or not 0 <= item["quantity"] <= 1000000
                        )
                    ):
                        raise ValueError
                    status(item["line_status"], optional=True)
                    money(item["sale_price"])
    except (ValueError, KeyError, TypeError, AttributeError, probe.ReadError):
        raise ERPError(
            "native_read_invalid", "Leitura nativa fora da projeção permitida."
        ) from None
