"""Fixed, bounded Orders 2026 read, executed inside the existing API container.

No native client helpers, refresh, credentials persistence or mutations. Buyer,
recipient, payment, tax, package and customized product data are never emitted.
"""

import asyncio
import base64
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from uuid import UUID

WINDOW = None
BASE = "https://sellingpartnerapi-na.amazon.com"
PATH = "/orders/2026-01-01/orders"
MARKETPLACE = "A2Q3Y263D00KWC"
LIMIT = 100
STATUSES = {
    "PENDING_AVAILABILITY",
    "PENDING",
    "UNSHIPPED",
    "PARTIALLY_SHIPPED",
    "SHIPPED",
    "CANCELLED",
    "UNFULFILLABLE",
    "INVOICE_UNCONFIRMED",
}


class OrderReadError(ValueError):
    def __init__(self, code, stage=None):
        super().__init__(code)
        self.stage = stage


def stamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
        return parsed.astimezone(UTC)
    except (ValueError, TypeError, AttributeError):
        raise OrderReadError("invalid_response", "dates") from None


def string(value, *, limit=256, optional=False):
    if optional and value in (None, ""):
        return None
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > limit
        or any(ord(c) < 32 for c in value)
    ):
        raise OrderReadError("invalid_response", "identifiers")
    return value


def money(value):
    if value is None:
        return None
    if not isinstance(value, dict) or not re.fullmatch(r"[A-Z]{3}", str(value.get("currencyCode"))):
        raise OrderReadError("invalid_response", "money")
    try:
        if not isinstance(value.get("amount"), str) or len(value["amount"]) > 64:
            raise ValueError
        amount = Decimal(value["amount"])
        if (
            not isinstance(value["amount"], str)
            or not amount.is_finite()
            or abs(amount) > Decimal("999999999")
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError, InvalidOperation):
        raise OrderReadError("invalid_response", "money") from None
    return {"amount": str(amount), "currency": value["currencyCode"]}


@dataclass(repr=False)
class OrderPlan:
    url: str
    headers: dict = field(repr=False)
    params: dict
    date_from: str
    date_to: str


def plan(credentials, now):
    token = credentials.get("access_token") if isinstance(credentials, dict) else None
    if (
        not isinstance(token, str)
        or not token
        or len(token) > 16384
        or any(ord(c) < 32 for c in token)
    ):
        raise OrderReadError("credentials_missing")
    if credentials.get("marketplace_id") != MARKETPLACE:
        raise OrderReadError("configuration_missing")
    # The documented upper boundary must be at least two minutes in the past.
    end, start = now - timedelta(minutes=3), now - timedelta(hours=24)
    if WINDOW:
        start = datetime.fromtimestamp(int(WINDOW["from"]), UTC)
        end = datetime.fromtimestamp(int(WINDOW["to"]), UTC)
    params = {
        "marketplaceIds": MARKETPLACE,
        "lastUpdatedAfter": start.isoformat(),
        "lastUpdatedBefore": end.isoformat(),
        "maxResultsPerPage": LIMIT,
        "includedData": "FULFILLMENT,PROCEEDS",
    }
    return OrderPlan(
        BASE + PATH,
        {"x-amz-access-token": token, "Accept": "application/json"},
        params,
        start.isoformat(),
        end.isoformat(),
    )


def project(body, read_plan):
    if not isinstance(body, dict) or not isinstance(body.get("orders"), list):
        raise OrderReadError("invalid_response")
    if len(body["orders"]) > LIMIT:
        raise OrderReadError("invalid_response")
    orders, seen = [], set()
    for raw in body["orders"]:
        if not isinstance(raw, dict):
            raise OrderReadError("invalid_response")
        oid = string(raw.get("orderId"), limit=40)
        if not re.fullmatch(r"\d{3}-\d{7}-\d{7}", oid) or oid in seen:
            raise OrderReadError("invalid_response")
        seen.add(oid)
        if (
            not isinstance(raw.get("salesChannel"), dict)
            or raw["salesChannel"].get("marketplaceId") != MARKETPLACE
        ):
            raise OrderReadError("marketplace_mismatch")
        created, updated = stamp(raw.get("createdTime")), stamp(raw.get("lastUpdatedTime"))
        fulfillment = raw.get("fulfillment") or {}
        status, fulfilled_by = fulfillment.get("fulfillmentStatus"), fulfillment.get("fulfilledBy")
        if status not in STATUSES or fulfilled_by not in {"MERCHANT", "AMAZON", None}:
            raise OrderReadError("invalid_response", "fulfillment")
        rows = raw.get("orderItems")
        if not isinstance(rows, list) or not 1 <= len(rows) <= 200:
            raise OrderReadError("invalid_response", "items")
        items, line_ids = [], set()
        for item in rows:
            quantity = item.get("quantityOrdered")
            # Preserve zero returned by the provider for reconciliation. This
            # observation is not an instruction to reserve or dispatch stock.
            if type(quantity) is not int or not 0 <= quantity <= 1000000:
                raise OrderReadError("invalid_response", "quantity")
            line_id = string(item.get("orderItemId"), limit=120)
            if line_id in line_ids:
                raise OrderReadError("invalid_response")
            line_ids.add(line_id)
            product, proceeds = item.get("product") or {}, item.get("proceeds") or {}
            prices = product.get("price") or {}
            items.append(
                {
                    "line_id": line_id,
                    "seller_sku": string(product.get("sellerSku"), optional=True),
                    "asin": string(product.get("asin"), limit=20, optional=True),
                    "quantity": quantity,
                    "unit_price": money(prices.get("unitPrice")),
                    "proceeds_total": money(proceeds.get("proceedsTotal")),
                    "measurement_review": bool(item.get("measurement")),
                }
            )
        grand_total = money((raw.get("proceeds") or {}).get("grandTotal"))
        orders.append(
            {
                "id": oid,
                "created_at": created.isoformat(),
                "updated_at": updated.isoformat(),
                "status": status,
                "fulfilled_by": fulfilled_by,
                "marketplace_id": MARKETPLACE,
                "total": grand_total,
                "items": items,
                "within_window": stamp(read_plan.date_from) <= updated <= stamp(read_plan.date_to),
            }
        )
    pagination = body.get("pagination") or {}
    if not isinstance(pagination, dict):
        raise OrderReadError("invalid_response")
    # Opaque page tokens remain on-server. This probe intentionally reads one page.
    return {"orders": orders, "has_more": bool(pagination.get("nextToken"))}


async def execute(read_plan, client):
    allowed = {
        "marketplaceIds",
        "lastUpdatedAfter",
        "lastUpdatedBefore",
        "maxResultsPerPage",
        "includedData",
    }
    if (
        read_plan.url != BASE + PATH
        or set(read_plan.params) != allowed
        or read_plan.params["maxResultsPerPage"] != LIMIT
        or read_plan.params["includedData"] != "FULFILLMENT,PROCEEDS"
        or read_plan.params["marketplaceIds"] != MARKETPLACE
        or read_plan.params["lastUpdatedAfter"] != read_plan.date_from
        or read_plan.params["lastUpdatedBefore"] != read_plan.date_to
        or not 0 < (stamp(read_plan.date_to) - stamp(read_plan.date_from)).total_seconds() <= 86400
    ):
        raise OrderReadError("configuration_missing")
    result = {
        "state": "invalid_response",
        "http_status": None,
        "orders": [],
        "has_more": None,
        "failure_stage": None,
    }
    try:
        async with client.stream(
            "GET", read_plan.url, params=read_plan.params, headers=read_plan.headers
        ) as response:
            result["http_status"] = response.status_code
            if response.status_code != 200:
                return {
                    **result,
                    "state": "auth_denied"
                    if response.status_code in {401, 403}
                    else "rate_limited"
                    if response.status_code == 429
                    else "provider_unavailable",
                }
            payload = bytearray()
            async for chunk in response.aiter_bytes():
                payload.extend(chunk)
                if len(payload) > 2 * 1024 * 1024:
                    return result
            body = json.loads(payload)
            return {**result, "state": "read_ok", **project(body, read_plan)}
    except OrderReadError as exc:
        return {**result, "state": str(exc), "failure_stage": exc.stage or "shape"}
    except (ValueError, TypeError, KeyError, AttributeError):
        return result
    except Exception:
        return {**result, "state": "provider_unavailable"}


async def collect(request):
    global WINDOW  # noqa: PLW0603
    WINDOW = request.get("window")
    import httpx
    from sqlalchemy import text

    from app.db import SessionLocal, engine
    from app.security.cipher import decrypt_json

    ids = [str(UUID(i)) for i in request["integration_ids"]]
    if not ids or len(ids) > 4 or len(set(ids)) != len(ids):
        raise OrderReadError("configuration_missing")
    logging.getLogger("httpx").setLevel(logging.CRITICAL)
    logging.getLogger("httpcore").setLevel(logging.CRITICAL)
    report = {
        "version": 4,
        "source": "amazon_orders_2026_read",
        "read_only": "on",
        "no_token_refresh": True,
        "external_business_writes": False,
        "accounts": [],
        "captured_at": None,
        "limit_per_account": LIMIT,
    }
    try:
        async with SessionLocal() as session:
            await session.execute(text("SET TRANSACTION READ ONLY"))
            if (await session.execute(text("SHOW transaction_read_only"))).scalar() != "on":
                raise OrderReadError("configuration_missing")
            rows = (
                (
                    await session.execute(
                        text(
                            "SELECT id,credentials,updated_at FROM davinci.integrations "
                            "WHERE id=ANY(CAST(:ids AS uuid[])) AND platform='amazon' "
                            "AND status='active' AND archived_at IS NULL ORDER BY id"
                        ),
                        {"ids": ids},
                    )
                )
                .mappings()
                .all()
            )
            await session.rollback()
        if {str(r["id"]) for r in rows} != set(ids):
            raise OrderReadError("configuration_missing")
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
            for row in rows:
                result = {
                    "integration_id": str(row["id"]),
                    "platform": "amazon",
                    "credentials_revision": row["updated_at"].isoformat(),
                    "checked_at": datetime.now(UTC).isoformat(),
                    "date_from": None,
                    "date_to": None,
                    "state": "credentials_missing",
                    "http_status": None,
                    "orders": [],
                    "has_more": None,
                    "failure_stage": None,
                }
                try:
                    try:
                        credentials = decrypt_json(row["credentials"])
                    except Exception:
                        raise OrderReadError("credential_unreadable") from None
                    read_plan = plan(credentials, datetime.now(UTC))
                    result.update(date_from=read_plan.date_from, date_to=read_plan.date_to)
                    result.update(await execute(read_plan, client))
                except OrderReadError as exc:
                    result["state"] = str(exc)
                report["accounts"].append(result)
    finally:
        report["captured_at"] = datetime.now(UTC).isoformat()
        await engine.dispose()
    return report


if __name__ == "__main__":
    request = json.loads(base64.b64decode(sys.argv[1], validate=True))
    print(json.dumps(asyncio.run(collect(request)), ensure_ascii=False))
