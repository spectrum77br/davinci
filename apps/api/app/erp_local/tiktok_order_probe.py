"""Fixed authorization GET and Orders search POST, with no business mutations.

Queries, tokens, ciphers, raw errors and personal fields stay on the API host.
This standalone script deliberately bypasses clients that may refresh tokens.
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
BASE = "https://open-api.tiktokglobalshop.com"
AUTH = "/authorization/202309/shops"
SEARCH = "/order/202309/orders/search"
PAGE_SIZE = 100
MAX_PAGES = 10
LIMIT = PAGE_SIZE * MAX_PAGES


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


def identifier(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ReadError("invalid_response", "identifiers")
    raw = str(value)
    if not re.fullmatch(r"[1-9]\d{0,19}", raw):
        raise ReadError("invalid_response", "identifiers")
    return raw


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
        if (
            len(raw) > 64
            or not isinstance(currency, str)
            or not re.fullmatch(r"[A-Z]{3}", currency)
        ):
            raise ValueError
        amount = Decimal(raw)
        if not amount.is_finite() or abs(amount) > Decimal("999999999"):
            raise ValueError
    except (ValueError, TypeError, InvalidOperation):
        raise ReadError("invalid_response", "money") from None
    return {"amount": str(amount), "currency": currency}


def response_data(body, status):
    if status in {401, 403}:
        raise ReadError("auth_denied", http_status=status)
    if status == 429:
        raise ReadError("rate_limited", http_status=status)
    if status != 200:
        raise ReadError("provider_unavailable", http_status=status)
    if not isinstance(body, dict) or type(body.get("code")) is not int:
        raise ReadError("invalid_response", "api_envelope", status)
    if body["code"] != 0:
        raise ReadError(
            "auth_denied"
            if body["code"] in {105002, 105005, 105006, 36009005}
            else "invalid_response",
            "api_code",
            status,
        )
    if not isinstance(body.get("data"), dict):
        raise ReadError("invalid_response", "api_envelope", status)
    return body["data"]


def request_plan(credentials, method, path, query, body, *, now):
    """Allow exactly two provider reads; the serialized body is also signed."""
    if (method, path) not in {("GET", AUTH), ("POST", SEARCH)}:
        raise ReadError("configuration_missing")
    if path == AUTH and (query or body is not None):
        raise ReadError("configuration_missing")
    if path == SEARCH:
        if (
            set(query) - {"page_token"}
            or not isinstance(body, dict)
            or set(body) != {"update_time_ge", "update_time_lt"}
        ):
            raise ReadError("configuration_missing")
        if (
            any(type(v) is not int for v in body.values())
            or not 0 < body["update_time_lt"] - body["update_time_ge"] <= 86400
        ):
            raise ReadError("configuration_missing")
        if "page_token" in query:
            string(query["page_token"], 4096)
    try:
        token, key, secret, cipher = [
            string(credentials.get(k), 16384)
            for k in ("access_token", "app_key", "app_secret", "shop_cipher")
        ]
    except (ReadError, AttributeError):
        raise ReadError("credentials_missing") from None
    params = {"app_key": key, "timestamp": str(now)}
    if path == SEARCH:
        params.update(
            shop_cipher=cipher,
            page_size=str(PAGE_SIZE),
            sort_field="update_time",
            sort_order="DESC",
            **query,
        )
    serialized = (
        json.dumps(body, separators=(",", ":"), ensure_ascii=False) if body is not None else None
    )
    material = path + "".join(k + params[k] for k in sorted(params)) + (serialized or "")
    params["sign"] = hmac.new(
        secret.encode(), (secret + material + secret).encode(), hashlib.sha256
    ).hexdigest()
    return {
        "method": method,
        "url": BASE + path,
        "params": params,
        "headers": {
            "x-tts-access-token": token,
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        "content": serialized.encode() if serialized is not None else None,
    }


def project(raw, shop_id, start, end):
    if not isinstance(raw, dict):
        raise ReadError("invalid_response", "shape")
    oid, status = identifier(raw.get("id")), string(raw.get("status"), 64)
    if not re.fullmatch(r"[A-Z_]+", status):
        raise ReadError("invalid_response", "fulfillment")
    created, updated = timestamp(raw.get("create_time")), timestamp(raw.get("update_time"))
    if created > updated:
        raise ReadError("invalid_response", "dates")
    payment = raw.get("payment") or {}
    if not isinstance(payment, dict):
        raise ReadError("invalid_response", "money")
    rows = raw.get("line_items")
    if not isinstance(rows, list) or len(rows) > 200:
        raise ReadError("invalid_response", "items")
    items, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            raise ReadError("invalid_response", "items")
        line = identifier(row.get("id"))
        if line in seen:
            raise ReadError("invalid_response", "identifiers")
        seen.add(line)
        quantity = row.get("quantity")
        if quantity is not None and (type(quantity) is not int or not 0 <= quantity <= 1000000):
            raise ReadError("invalid_response", "quantity")
        item_status = string(row.get("display_status"), 64, optional=True)
        if item_status and not re.fullmatch(r"[A-Z_]+", item_status):
            raise ReadError("invalid_response", "fulfillment")
        items.append(
            {
                "line_id": line,
                "listing_id": identifier(row.get("product_id")),
                "variation_id": identifier(row.get("sku_id")),
                "seller_sku": string(row.get("seller_sku"), optional=True),
                "quantity": quantity,
                "quantity_review": quantity is None,
                "sale_price": monetary(
                    row.get("sale_price"), row.get("currency") or payment.get("currency")
                ),
                "line_status": item_status,
                "composition_review": bool(
                    row.get("combined_listing_skus") or row.get("sub_item_info")
                ),
            }
        )
    return {
        "id": oid,
        "shop_id": shop_id,
        "created_at": created,
        "updated_at": updated,
        "status": status,
        "fulfilled_by": {
            "FULFILLMENT_BY_SELLER": "MERCHANT",
            "FULFILLMENT_BY_TIKTOK": "TIKTOK",
        }.get(raw.get("fulfillment_type")),
        "total": monetary(payment.get("total_amount"), payment.get("currency")),
        "items": items,
        "within_window": start <= raw["update_time"] < end,
    }


async def read_account(client, credentials, *, now):
    end, start = int(now.timestamp()) - 3, int(now.timestamp()) - 86400
    if WINDOW:
        start, end = int(WINDOW["from"]), int(WINDOW["to"])

    async def read(method, path, query=None, body=None):
        plan = request_plan(credentials, method, path, query or {}, body, now=int(time.time()))
        async with client.stream(**plan) as response:
            if response.status_code != 200:
                response_data(None, response.status_code)
            payload = bytearray()
            async for chunk in response.aiter_bytes():
                payload.extend(chunk)
                if len(payload) > 2 * 1024 * 1024:
                    raise ReadError("invalid_response", "shape", 200)
            try:
                body = json.loads(payload, parse_float=Decimal)
            except (ValueError, UnicodeError):
                raise ReadError("invalid_response", "shape", 200) from None
            return response_data(body, response.status_code)

    shops = (await read("GET", AUTH)).get("shops")
    if (
        not isinstance(shops, list)
        or len(shops) > 100
        or any(not isinstance(s, dict) for s in shops)
    ):
        raise ReadError("invalid_response", "authorization_shape", 200)
    matches = [s for s in shops if s.get("cipher") == credentials["shop_cipher"]]
    if len(matches) != 1:
        raise ReadError("identity_mismatch", "identifiers", 200)
    shop_id = identifier(matches[0].get("id"))
    if credentials.get("shop_id") and identifier(credentials["shop_id"]) != shop_id:
        raise ReadError("identity_mismatch", "identifiers", 200)
    orders, seen, tokens, totals = [], set(), set(), set()
    next_token, pages_read = None, 0
    for _ in range(MAX_PAGES):
        data = await read(
            "POST",
            SEARCH,
            {"page_token": next_token} if next_token else {},
            {"update_time_ge": start, "update_time_lt": end},
        )
        rows = data.get("orders")
        total = data.get("total_count")
        # The channel omits the array in an explicitly empty result. A missing
        # array with a nonzero/missing count or a cursor is still invalid.
        if rows is None and type(total) is int and total == 0 and not data.get("next_page_token"):
            rows = []
        if not isinstance(rows, list) or len(rows) > PAGE_SIZE:
            raise ReadError("invalid_response", "order_list", 200)
        if type(total) is not int or not 0 <= total <= 10000000:
            raise ReadError("invalid_response", "order_count", 200)
        totals.add(total)
        pages_read += 1
        for raw in rows:
            order = project(raw, shop_id, start, end)
            if order["id"] in seen:
                raise ReadError("invalid_response", "identifiers", 200)
            seen.add(order["id"])
            orders.append(order)
        next_token = data.get("next_page_token")
        if next_token in (None, ""):
            break
        string(next_token, 4096)
        if next_token in tokens or not rows:
            raise ReadError("invalid_response", "shape", 200)
        tokens.add(next_token)
        await asyncio.sleep(0.25)
    coverage_review = len(totals) != 1 or not next_token and total != len(orders)
    return {
        "state": "partial_read" if coverage_review else "read_ok",
        "http_status": 200,
        "orders": orders,
        "has_more": bool(next_token),
        "pages_read": pages_read,
        "total_eligible": total,
        "coverage_review": bool(coverage_review),
        "identity_match": True,
        "shop_id": shop_id,
        "date_from": datetime.fromtimestamp(start, UTC).isoformat(),
        "date_to": datetime.fromtimestamp(end, UTC).isoformat(),
        "failure_stage": None,
    }


async def collect(request):
    global WINDOW  # noqa: PLW0603
    WINDOW = request.get("window")
    import httpx
    from sqlalchemy import text

    from app.db import SessionLocal, engine
    from app.security.cipher import decrypt_json

    ids = [str(UUID(i)) for i in request["integration_ids"]]
    if not 1 <= len(ids) <= 8 or len(set(ids)) != len(ids):
        raise ReadError("configuration_missing")
    report = {
        "version": 1,
        "source": "tiktok_orders_202309_read",
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
                            "WHERE id=ANY(CAST(:ids AS uuid[])) AND platform='tiktok' "
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
        started = time.monotonic()
        async with httpx.AsyncClient(timeout=25, follow_redirects=False) as client:
            for row in rows:
                checked = datetime.now(UTC)
                result = {
                    "integration_id": str(row["id"]),
                    "platform": "tiktok",
                    "credentials_revision": row["updated_at"].isoformat(),
                    "checked_at": checked.isoformat(),
                    "state": "not_run",
                    "http_status": None,
                    "orders": [],
                    "has_more": None,
                    "pages_read": 0,
                    "total_eligible": None,
                    "coverage_review": False,
                    "identity_match": None,
                    "shop_id": None,
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
                            result.update(await read_account(client, credentials, now=checked))
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
