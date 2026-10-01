"""Read-only confirmation of a complete Magalu order shipment.

Delivery states, rather than the order's ``approved`` state, confirm dispatch.
No local orders, stock, notifications or marketplace states are changed here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.services.marketplaces.magalu import MagaluClient

_PAGE_LIMIT = 10
_SHIPPED = frozenset({"shipped", "delivered"})
_BLOCKED_ORDER = frozenset({"cancelled", "canceled", "frozen"})
_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?"
    r"(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)$"
)


@dataclass(frozen=True, slots=True)
class MagaluShipmentStatus:
    confirmed: bool = False
    shipped_at: datetime | None = None
    deadline: datetime | None = None
    reason: str | None = None


def normalize_magalu_order_code(order_code: str | None) -> str | None:
    """Remove only Bling's optional LU- prefix; keep internal separators."""
    if not isinstance(order_code, str) or not order_code or order_code.isspace():
        return None
    code = order_code[3:] if order_code[:3].lower() == "lu-" else order_code
    return code if code and not code.isspace() else None


def _utc_timestamp(value: Any) -> datetime | None:
    # Date-only and RFC3339's unknown offset (-00:00) cannot locate an instant.
    if not isinstance(value, str) or not _TIMESTAMP.fullmatch(value):
        return None
    if value.endswith("-00:00"):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except (ValueError, OverflowError):
        return None


def _nonempty_id(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _complete_page(payload: dict[str, Any]) -> bool:
    meta = payload.get("meta")
    if not isinstance(meta, dict) or not isinstance(meta.get("page"), dict):
        return False
    page = meta["page"]
    # We requested the first ten matches. A full/truncated page cannot prove
    # uniqueness, even if a broken response contains only one result.
    for key in ("offset", "count", "limit"):
        if type(page.get(key)) is not int:
            return False
    if page["offset"] != 0 or page["count"] != 1 or page["limit"] != _PAGE_LIMIT:
        return False
    for key in ("total", "total_count"):
        if key in page and (type(page[key]) is not int or page[key] != 1):
            return False
    for links in (meta.get("links", {}), payload.get("links", {})):
        if not isinstance(links, dict):
            return False
        if any(links.get(key) for key in ("next", "previous", "prev")):
            return False
    return True


def _dispatch_deadline(deliveries: list[dict[str, Any]]) -> datetime | None:
    deadlines = []
    for delivery in deliveries:
        shipping = delivery.get("shipping")
        handling = shipping.get("handling_time") if isinstance(shipping, dict) else None
        deadline = (
            _utc_timestamp(handling.get("limit_date")) if isinstance(handling, dict) else None
        )
        if deadline is None:
            return None
        deadlines.append(deadline)
    # shipping.deadline is the customer's delivery deadline, not dispatch.
    return min(deadlines)


def parse_magalu_shipment_status(
    payload: Any,
    order_code: str | None,
    *,
    now: datetime | None = None,
) -> MagaluShipmentStatus:
    """Confirm one exact order only when every identified delivery has left.

    Missing shipped_at is different from an invalid supplied timestamp: status
    alone may confirm dispatch, but cannot supply the complete order's time.
    A dated complete shipment uses the latest delivery's actual dispatch time.
    Orders document shipping.posting_date; delivery detail uses shipped_at.
    shipping.shipped_at is retained for the observed orders response shape.
    """
    code = normalize_magalu_order_code(order_code)
    if code is None:
        return MagaluShipmentStatus(reason="invalid_code")
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        return MagaluShipmentStatus(reason="invalid_response")
    orders = payload["results"]
    if len(orders) != 1:
        return MagaluShipmentStatus(reason="ambiguous_order")
    if not _complete_page(payload):
        return MagaluShipmentStatus(reason="pagination_ambiguous")
    order = orders[0]
    if not isinstance(order, dict) or not _nonempty_id(order.get("id")):
        return MagaluShipmentStatus(reason="invalid_order")
    # Only the local code is normalized: do not repair identity in a response.
    if order.get("code") != code:
        return MagaluShipmentStatus(reason="code_mismatch")
    status = order.get("status")
    if isinstance(status, str) and status.strip().lower() in _BLOCKED_ORDER:
        return MagaluShipmentStatus(reason="order_not_shipped")
    deliveries = order.get("deliveries")
    if not isinstance(deliveries, list) or not deliveries:
        return MagaluShipmentStatus(reason="invalid_deliveries")
    seen = set()
    for delivery in deliveries:
        if not isinstance(delivery, dict) or not _nonempty_id(delivery.get("id")):
            return MagaluShipmentStatus(reason="invalid_deliveries")
        identity = delivery["id"].strip().lower()
        if identity in seen:
            return MagaluShipmentStatus(reason="invalid_deliveries")
        seen.add(identity)
    deadline = _dispatch_deadline(deliveries)
    if any(
        not isinstance(delivery.get("status"), str)
        or delivery["status"].strip().lower() not in _SHIPPED
        for delivery in deliveries
    ):
        return MagaluShipmentStatus(deadline=deadline, reason="not_shipped")
    now = now if now is not None else datetime.now(UTC)
    if now.tzinfo is None or now.utcoffset() is None:
        return MagaluShipmentStatus(reason="invalid_now")
    timestamps = []
    for delivery in deliveries:
        shipping = delivery.get("shipping")
        if shipping is not None and not isinstance(shipping, dict):
            return MagaluShipmentStatus(reason="invalid_shipped_at")
        shipping = shipping or {}
        supplied = (
            delivery.get("shipped_at"),
            shipping.get("posting_date"),
            shipping.get("shipped_at"),
        )
        delivery_timestamps = []
        for raw in supplied:
            if raw is None:
                continue
            timestamp = _utc_timestamp(raw)
            if timestamp is None:
                return MagaluShipmentStatus(reason="invalid_shipped_at")
            if timestamp > now:
                return MagaluShipmentStatus(reason="future_shipped_at")
            delivery_timestamps.append(timestamp)
        if len(set(delivery_timestamps)) > 1:
            return MagaluShipmentStatus(reason="conflicting_shipped_at")
        if delivery_timestamps:
            timestamps.append(delivery_timestamps[0])
    shipped_at = max(timestamps) if len(timestamps) == len(deliveries) else None
    return MagaluShipmentStatus(confirmed=True, shipped_at=shipped_at, deadline=deadline)


async def get_magalu_shipment_status(
    client: MagaluClient,
    order_code: str | None,
    *,
    now: datetime | None = None,
) -> MagaluShipmentStatus:
    """Fetch just one code; failures never imply shipment or expose a body."""
    code = normalize_magalu_order_code(order_code)
    if code is None:
        return MagaluShipmentStatus(reason="invalid_code")
    try:
        response = await client._request(
            "GET", "/seller/v1/orders", params={"code": code, "_limit": _PAGE_LIMIT, "_offset": 0}
        )
    except Exception:  # noqa: BLE001 — network/token failures must fail closed
        return MagaluShipmentStatus(reason="request_failed")
    if response.status_code != 200:
        return MagaluShipmentStatus(reason="http_error")
    try:
        payload = response.json()
    except ValueError:
        return MagaluShipmentStatus(reason="invalid_response")
    return parse_magalu_shipment_status(payload, order_code, now=now)
