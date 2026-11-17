"""Strict shape of a Temu read taken from DaVinci's copy of Bling (temu_order_probe)."""

import re
from datetime import UTC, datetime, timedelta

from app.erp_local import temu_order_probe as probe
from app.erp_local.core import ERPError

ACCOUNT_FIELDS = {
    "integration_id",
    "platform",
    "checked_at",
    "date_from",
    "date_to",
    "state",
    "orders",
    "has_more",
}
ORDER_FIELDS = {
    "id",
    "bling_number",
    "created_at",
    "updated_at",
    "status",
    "bling_situation",
    "total",
    "products_total",
    "items",
}
ITEM_FIELDS = {"line_id", "seller_sku", "quantity", "unit_price", "discount"}
STATES = {"read_ok", "configuration_missing", "provider_unavailable"}
AMOUNT = re.compile(r"-?\d{1,12}\.\d{2}")


def _stamp(value):
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None:
        raise ValueError
    return moment.astimezone(UTC)


def _money(value):
    if (
        not isinstance(value, dict)
        or set(value) != {"amount", "currency"}
        or value["currency"] != "BRL"
        or not isinstance(value["amount"], str)
        or not AMOUNT.fullmatch(value["amount"])
    ):
        raise ValueError


def _order(order, start, end):
    if not isinstance(order, dict) or set(order) != ORDER_FIELDS:
        raise ValueError
    if not isinstance(order["id"], str) or not probe.NUMBER.fullmatch(order["id"]):
        raise ValueError
    if not isinstance(order["bling_number"], str) or not re.fullmatch(
        r"\d{1,20}", order["bling_number"]
    ):
        raise ValueError
    if order["status"] not in {"BLING_COPY", "CANCELLED"} or not isinstance(
        order["bling_situation"], str
    ):
        raise ValueError
    if not re.fullmatch(r"\d{0,12}", order["bling_situation"]) or (
        order["status"] == "CANCELLED"
    ) != (order["bling_situation"] == probe.CANCELLED):
        raise ValueError
    updated = _stamp(order["updated_at"])
    if _stamp(order["created_at"]) > updated or not start < updated <= end:
        raise ValueError
    _money(order["total"])
    _money(order["products_total"])
    if not isinstance(order["items"], list) or len(order["items"]) > 200:
        raise ValueError
    lines = set()
    for item in order["items"]:
        if not isinstance(item, dict) or set(item) != ITEM_FIELDS:
            raise ValueError
        if not isinstance(item["line_id"], str) or not re.fullmatch(r"\d{1,4}", item["line_id"]):
            raise ValueError
        if item["line_id"] in lines:
            raise ValueError
        lines.add(item["line_id"])
        sku = item["seller_sku"]
        if sku is not None and (not isinstance(sku, str) or not 0 < len(sku) <= 120):
            raise ValueError
        if type(item["quantity"]) is not int or not 0 <= item["quantity"] <= 1000000:
            raise ValueError
        _money(item["unit_price"])
        _money(item["discount"])


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
                "captured_at",
                "limit_per_account",
                "accounts",
            }
            or read["version"] != 1
            or type(read["version"]) is not int
            or read["source"] != probe.SOURCE
            or read["read_only"] != "on"
            or read["no_token_refresh"] is not True
            or read["external_business_writes"] is not False
            or read["limit_per_account"] != probe.LIMIT
        ):
            raise ValueError
        captured = _stamp(read["captured_at"])
        if captured > datetime.now(UTC) + timedelta(minutes=5):
            raise ValueError
        if not isinstance(read["accounts"], list) or not 1 <= len(read["accounts"]) <= 4:
            raise ValueError
        seen = set()
        for account in read["accounts"]:
            if (
                not isinstance(account, dict)
                or set(account) != ACCOUNT_FIELDS
                or account["platform"] != "temu"
                or account["state"] not in STATES
                or account["integration_id"] in seen
                or not isinstance(account["integration_id"], str)
                or not isinstance(account["orders"], list)
            ):
                raise ValueError
            seen.add(account["integration_id"])
            start, end = _stamp(account["date_from"]), _stamp(account["date_to"])
            if _stamp(account["checked_at"]) > captured or not start < end:
                raise ValueError
            if account["state"] != "read_ok":
                if account["orders"] or account["has_more"] is not None:
                    raise ValueError
                continue
            if (
                account["integration_id"] not in probe.STORES
                or type(account["has_more"]) is not bool
                or (end - start).total_seconds() > 86400
                or len(account["orders"]) > probe.LIMIT
            ):
                raise ValueError
            orders = set()
            for order in account["orders"]:
                _order(order, start, end)
                if order["id"] in orders:
                    raise ValueError
                orders.add(order["id"])
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ERPError(
            "native_read_invalid", "Leitura nativa fora da projeção permitida."
        ) from None
