"""Bounded GET-only Shopee list/detail reader. No refresh or business writes.

The raw response remains on the API host. Only the explicit order/item
projection below may leave it; signed URLs and remote error strings never do.
"""

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import re
import sys
import time
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID

WINDOW = None
BASE = "https://partner.shopeemobile.com"
LIST = "/api/v2/order/get_order_list"
DETAIL = "/api/v2/order/get_order_detail"
PAGE_SIZE = 100
MAX_PAGES = 10
LIMIT = PAGE_SIZE * MAX_PAGES
DETAIL_LIMIT = 50
OPTIONAL = "order_status,create_time,update_time,currency,total_amount,item_list,fulfillment_flag"


class ReadError(ValueError):
    def __init__(self, code, stage=None, http_status=None):
        super().__init__(code)
        self.stage, self.http_status = stage, http_status


def string(value, limit=256, optional=False):
    if optional and value in (None, ""):
        return None
    if (
        not isinstance(value, str)
        or not value
        or len(value) > limit
        or any(ord(c) < 32 for c in value)
    ):
        raise ReadError("invalid_response", "identifiers")
    return value


def identifier(value, *, zero=False):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ReadError("invalid_response", "identifiers")
    raw = str(value)
    if not re.fullmatch(r"\d{1,20}", raw) or int(raw) < (0 if zero else 1):
        raise ReadError("invalid_response", "identifiers")
    return str(int(raw))


def timestamp(value):
    if type(value) is not int or not 0 < value <= 4102444800:
        raise ReadError("invalid_response", "dates")
    return datetime.fromtimestamp(value, UTC).isoformat()


def monetary(value, currency):
    if value is None:
        return None
    try:
        if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
            raise ValueError
        raw = str(value)
        if len(raw) > 64 or not re.fullmatch(r"[A-Z]{3}", currency):
            raise ValueError
        amount = Decimal(raw)
        if not amount.is_finite() or abs(amount) > Decimal("999999999"):
            raise ValueError
    except (ValueError, TypeError, InvalidOperation):
        raise ReadError("invalid_response", "money") from None
    return {"amount": str(amount), "currency": currency}


def order_id(value):
    value = string(value, 64)
    if not re.fullmatch(r"[A-Za-z0-9-]+", value):
        raise ReadError("invalid_response", "identifiers")
    return value


def cursor(value):
    if not isinstance(value, str) or len(value) > 4096 or any(ord(c) < 32 for c in value):
        raise ReadError("invalid_response", "shape")
    return value


def parameters(path, credentials, query, *, now, partner_id, partner_key):
    allowed = (
        {
            "time_range_field",
            "time_from",
            "time_to",
            "page_size",
            "cursor",
            "response_optional_fields",
        }
        if path == LIST
        else {"order_sn_list", "response_optional_fields"}
        if path == DETAIL
        else set()
    )
    if not allowed or set(query) != allowed:
        raise ReadError("configuration_missing")
    if path == LIST and (
        query["time_range_field"] != "update_time"
        or query["page_size"] != PAGE_SIZE
        or query["response_optional_fields"] != "order_status"
        or not 0 < query["time_to"] - query["time_from"] <= 86400
    ):
        raise ReadError("configuration_missing")
    if path == LIST:
        cursor(query["cursor"])
    if path == DETAIL:
        ids = query["order_sn_list"].split(",")
        if not 1 <= len(ids) <= DETAIL_LIMIT or query["response_optional_fields"] != OPTIONAL:
            raise ReadError("configuration_missing")
        for value in ids:
            order_id(value)
    try:
        token = string(credentials.get("access_token"), 16384)
        shop = identifier(credentials.get("shop_id"))
        pid = identifier(credentials.get("partner_id") or partner_id)
        secret = string(credentials.get("partner_key") or partner_key, 16384)
    except ReadError:
        raise ReadError("credentials_missing") from None
    signature = hmac.new(
        secret.encode(), f"{pid}{path}{now}{token}{shop}".encode(), hashlib.sha256
    ).hexdigest()
    return {
        **query,
        "partner_id": pid,
        "timestamp": now,
        "access_token": token,
        "shop_id": shop,
        "sign": signature,
    }


def response_data(body, status):
    if status in {401, 403}:
        raise ReadError("auth_denied", http_status=status)
    if status == 429:
        raise ReadError("rate_limited", http_status=status)
    if status != 200:
        raise ReadError("provider_unavailable", http_status=status)
    if not isinstance(body, dict):
        raise ReadError("invalid_response", "shape", status)
    error = body.get("error")
    if error not in (None, "", 0) or isinstance(error, bool):
        auth = isinstance(error, str) and any(
            word in error.lower() for word in ["token", "auth", "permission", "sign"]
        )
        raise ReadError("auth_denied" if auth else "invalid_response", "shape", status)
    if not isinstance(body.get("response"), dict):
        raise ReadError("invalid_response", "shape", status)
    return body["response"]


def project(order, shop_id, start, end):
    if not isinstance(order, dict):
        raise ReadError("invalid_response", "shape")
    oid = order_id(order.get("order_sn"))
    status = string(order.get("order_status"), 64)
    if not re.fullmatch(r"[A-Z_]+", status):
        raise ReadError("invalid_response", "fulfillment")
    currency = string(order.get("currency"), 3)
    created, updated = timestamp(order.get("create_time")), timestamp(order.get("update_time"))
    if created > updated:
        raise ReadError("invalid_response", "dates")
    rows = order.get("item_list")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 200:
        raise ReadError("invalid_response", "items")
    items = []
    for index, raw in enumerate(rows):
        listing = identifier(raw.get("item_id"))
        model = identifier(raw.get("model_id", 0), zero=True)
        quantity = raw.get("model_quantity_purchased")
        if type(quantity) is not int or not 0 <= quantity <= 1000000:
            raise ReadError("invalid_response", "quantity")
        # A blank variation SKU is not replaced by the parent SKU. Exact item /
        # model identity can still be reconciled against the account listing.
        sku = raw.get("model_sku") if model != "0" else raw.get("item_sku")
        items.append(
            {
                "line_id": f"{listing}:{model}:{index}",
                "listing_id": listing,
                "variation_id": model,
                "seller_sku": string(sku, optional=True),
                "quantity": quantity,
                "unit_price": monetary(raw.get("model_discounted_price"), currency),
            }
        )
    flag = order.get("fulfillment_flag")
    fulfilled = (
        "SHOPEE"
        if flag == "fulfilled_by_shopee"
        else "MERCHANT"
        if flag == "fulfilled_by_local_seller"
        else None
    )
    return {
        "id": oid,
        "created_at": created,
        "updated_at": updated,
        "status": status,
        "fulfilled_by": fulfilled,
        "shop_id": shop_id,
        "total": monetary(order.get("total_amount"), currency),
        "items": items,
        "within_window": start <= order["update_time"] <= end,
    }


async def read_account(client, credentials, *, partner_id, partner_key, now):
    end, start = int(now.timestamp()) - 3, int(now.timestamp()) - 86400
    if WINDOW:
        start, end = int(WINDOW["from"]), int(WINDOW["to"])

    async def get(path, query):
        params = parameters(
            path,
            credentials,
            query,
            now=int(time.time()),
            partner_id=partner_id,
            partner_key=partner_key,
        )
        async with client.stream(
            "GET", BASE + path, params=params, headers={"Accept": "application/json"}
        ) as response:
            if response.status_code != 200:
                response_data(None, response.status_code)
            payload = bytearray()
            async for chunk in response.aiter_bytes():
                payload.extend(chunk)
                if len(payload) > 2 * 1024 * 1024:
                    raise ReadError("invalid_response", "shape", response.status_code)
            try:
                body = json.loads(payload, parse_float=Decimal)
            except (ValueError, UnicodeError):
                raise ReadError("invalid_response", "shape", response.status_code) from None
            return response_data(body, response.status_code)

    projected, seen, listed, cursors = [], set(), set(), {""}
    next_cursor, pages_read = "", 0
    for _ in range(MAX_PAGES):
        data = await get(
            LIST,
            {
                "time_range_field": "update_time",
                "time_from": start,
                "time_to": end,
                "page_size": PAGE_SIZE,
                "cursor": next_cursor,
                "response_optional_fields": "order_status",
            },
        )
        rows = data.get("order_list")
        if (
            not isinstance(rows, list)
            or len(rows) > PAGE_SIZE
            or type(data.get("more")) is not bool
        ):
            raise ReadError("invalid_response", "shape", 200)
        ids = [order_id(r.get("order_sn")) for r in rows]
        if len(set(ids)) != len(ids) or listed.intersection(ids):
            raise ReadError("invalid_response", "identifiers", 200)
        listed.update(ids)
        for offset in range(0, len(ids), DETAIL_LIMIT):
            batch = ids[offset : offset + DETAIL_LIMIT]
            await asyncio.sleep(0.25)
            detail = await get(
                DETAIL, {"order_sn_list": ",".join(batch), "response_optional_fields": OPTIONAL}
            )
            orders = detail.get("order_list")
            if not isinstance(orders, list) or len(orders) > DETAIL_LIMIT:
                raise ReadError("invalid_response", "shape", 200)
            for raw in orders:
                item = project(raw, identifier(credentials.get("shop_id")), start, end)
                if item["id"] not in batch or item["id"] in seen:
                    raise ReadError("invalid_response", "identifiers", 200)
                seen.add(item["id"])
                projected.append(item)
        pages_read += 1
        if not data["more"]:
            break
        next_cursor = cursor(data.get("next_cursor"))
        if not ids or not next_cursor or next_cursor in cursors:
            raise ReadError("invalid_response", "shape", 200)
        cursors.add(next_cursor)
    missing = len(listed - seen)
    return {
        "state": "partial_read" if missing else "read_ok",
        "http_status": 200,
        "orders": projected,
        "has_more": data["more"],
        "pages_read": pages_read,
        "missing_details": missing,
        "date_from": datetime.fromtimestamp(start, UTC).isoformat(),
        "date_to": datetime.fromtimestamp(end, UTC).isoformat(),
        "failure_stage": None,
    }


async def collect(request):
    global WINDOW  # noqa: PLW0603
    WINDOW = request.get("window")
    import httpx
    from sqlalchemy import text

    from app.config import get_settings
    from app.db import SessionLocal, engine
    from app.security.cipher import decrypt_json

    ids = [str(UUID(i)) for i in request["integration_ids"]]
    if not 1 <= len(ids) <= 16 or len(set(ids)) != len(ids):
        raise ReadError("configuration_missing")
    report = {
        "version": 2,
        "source": "shopee_orders_v2_read",
        "read_only": "on",
        "no_token_refresh": True,
        "external_business_writes": False,
        "limit_per_account": LIMIT,
        "captured_at": None,
        "accounts": [],
    }
    logging.getLogger("httpx").setLevel(logging.CRITICAL)
    logging.getLogger("httpcore").setLevel(logging.CRITICAL)
    try:
        async with SessionLocal() as session:
            await session.execute(text("SET TRANSACTION READ ONLY"))
            if (await session.execute(text("SHOW transaction_read_only"))).scalar() != "on":
                raise ReadError("configuration_missing")
            rows = (
                (
                    await session.execute(
                        text(
                            "SELECT id,credentials,updated_at FROM davinci.integrations "
                            "WHERE id=ANY(CAST(:ids AS uuid[])) AND platform='shopee' "
                            "AND status='active' "
                            "AND archived_at IS NULL ORDER BY id"
                        ),
                        {"ids": ids},
                    )
                )
                .mappings()
                .all()
            )
            await session.rollback()
        if {str(r["id"]) for r in rows} != set(ids):
            raise ReadError("configuration_missing")
        settings = get_settings()
        started = time.monotonic()
        async with httpx.AsyncClient(timeout=25, follow_redirects=False) as client:
            for row in rows:
                result = {
                    "integration_id": str(row["id"]),
                    "platform": "shopee",
                    "credentials_revision": row["updated_at"].isoformat(),
                    "checked_at": datetime.now(UTC).isoformat(),
                    "state": "not_run",
                    "http_status": None,
                    "orders": [],
                    "has_more": None,
                    "missing_details": 0,
                    "pages_read": 0,
                    "date_from": None,
                    "date_to": None,
                    "failure_stage": None,
                }
                if time.monotonic() - started < 210:
                    try:
                        try:
                            credentials = decrypt_json(row["credentials"])
                        except Exception:
                            raise ReadError("credential_unreadable") from None
                        async with asyncio.timeout(210 - (time.monotonic() - started)):
                            result.update(
                                await read_account(
                                    client,
                                    credentials,
                                    partner_id=settings.shopee_partner_id,
                                    partner_key=settings.shopee_partner_key,
                                    now=datetime.now(UTC),
                                )
                            )
                    except ReadError as exc:
                        result.update(
                            state=str(exc), failure_stage=exc.stage, http_status=exc.http_status
                        )
                    except Exception:
                        result.update(state="provider_unavailable")
                report["accounts"].append(result)
    finally:
        report["captured_at"] = datetime.now(UTC).isoformat()
        await engine.dispose()
    return report


if __name__ == "__main__":
    request = json.loads(base64.b64decode(sys.argv[1], validate=True))
    print(json.dumps(asyncio.run(collect(request)), ensure_ascii=False))
