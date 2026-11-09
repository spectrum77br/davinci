"""Bounded OR11 GET reader. Never invokes A01, refreshes keys or writes orders.

A01 has a separate daily verification policy. Its existing credential-bound
server cache may be reported as prior evidence, never as a new identity check.
"""

import asyncio
import base64
import hashlib
import json
import logging
import re
import sys
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID

WINDOW = None
BASE = "https://marketplace.carrefour.com.br/api"
ORDERS = "/orders"
PAGE_SIZE, MAX_PAGES = 100, 10
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


def positive_id(value):
    if (
        isinstance(value, bool)
        or not isinstance(value, (str, int))
        or not re.fullmatch(r"[1-9]\d{0,19}", str(value))
    ):
        raise ReadError("invalid_response", "identifiers")
    return str(value)


def stamp(value):
    try:
        date = datetime.fromisoformat(string(value, 64).replace("Z", "+00:00"))
        if date.tzinfo is None or date.utcoffset() is None or value.endswith("-00:00"):
            raise ValueError
        return date.astimezone(UTC)
    except (ValueError, TypeError, AttributeError):
        raise ReadError("invalid_response", "dates") from None


def monetary(value, currency):
    if value is None:
        return None
    try:
        if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
            raise ValueError
        raw = str(value)
        amount = Decimal(raw)
        if (
            len(raw) > 64
            or not amount.is_finite()
            or abs(amount) > Decimal("999999999")
            or not isinstance(currency, str)
            or not re.fullmatch(r"[A-Z]{3}", currency)
        ):
            raise ValueError
    except (ValueError, TypeError, InvalidOperation):
        raise ReadError("invalid_response", "money") from None
    return {"amount": str(amount), "currency": currency}


def identity_cache(credentials, *, now):
    result = {
        "identity_match": None,
        "identity_basis": "configured_shop_parameter",
        "identity_checked_at": None,
    }
    check = credentials.get("_carrefour_account_check")
    shop, key = (
        positive_id(credentials.get("shop_id")),
        string(credentials.get("api_key"), 16384).strip(),
    )
    fingerprint = hashlib.sha256(f"{shop}\0{key}".encode()).hexdigest()
    if (
        not isinstance(check, dict)
        or check.get("fingerprint") != fingerprint
        or type(check.get("checked_at")) is not int
        or not 0 <= now - check["checked_at"] < 86400
        or check.get("ok") is not True
    ):
        return result
    info = check.get("info")
    if (
        not isinstance(info, dict)
        or str(info.get("shop_id")) != shop
        or info.get("shop_state") != "OPEN"
    ):
        return result
    return {
        "identity_match": True,
        "identity_basis": "server_daily_cache",
        "identity_checked_at": datetime.fromtimestamp(check["checked_at"], UTC).isoformat(),
    }


def parameters(credentials, path, *, start, end, offset):
    if (
        path != ORDERS
        or type(offset) is not int
        or offset < 0
        or offset >= LIMIT
        or not 0 < (stamp(end) - stamp(start)).total_seconds() <= 86400
    ):
        raise ReadError("configuration_missing")
    try:
        shop, key = (
            positive_id(credentials.get("shop_id")),
            string(credentials.get("api_key"), 16384).strip(),
        )
        if not key.isascii() or any(ord(c) < 33 or ord(c) == 127 for c in key):
            raise ValueError
    except (ReadError, AttributeError, ValueError):
        raise ReadError("credentials_missing") from None
    return {
        "shop_id": shop,
        "start_update_date": start,
        "end_update_date": end,
        "max": PAGE_SIZE,
        "offset": offset,
        "order_tax_mode": "TAX_INCLUDED",
    }, {"Authorization": key, "Accept": "application/json"}


def project(raw, shop_id, start, end):
    if not isinstance(raw, dict):
        raise ReadError("invalid_response", "shape")
    created, updated = stamp(raw.get("created_date")), stamp(raw.get("last_updated_date"))
    if created > updated:
        raise ReadError("invalid_response", "dates")
    status = string(raw.get("order_state"), 64)
    if not re.fullmatch(r"[A-Z_]+", status):
        raise ReadError("invalid_response", "fulfillment")
    tax_mode = raw.get("order_tax_mode")
    if tax_mode not in {None, "TAX_INCLUDED", "TAX_EXCLUDED"}:
        raise ReadError("invalid_response", "money")
    currency = raw.get("currency_iso_code")
    rows = raw.get("order_lines")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 200:
        raise ReadError("invalid_response", "items")
    items, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            raise ReadError("invalid_response", "items")
        line = string(row.get("order_line_id"), 120)
        quantity = row.get("quantity")
        if line in seen or type(quantity) is not int or not 0 <= quantity <= 1000000:
            raise ReadError("invalid_response", "quantity")
        seen.add(line)
        line_status = string(row.get("order_line_state"), 64, optional=True)
        if line_status and not re.fullmatch(r"[A-Z_]+", line_status):
            raise ReadError("invalid_response", "fulfillment")
        items.append(
            {
                "line_id": line,
                "listing_id": positive_id(row.get("offer_id")),
                "variation_id": None,
                "seller_sku": string(row.get("offer_sku"), optional=True),
                "quantity": quantity,
                "unit_price": monetary(row.get("price_unit"), currency),
                "line_status": line_status,
                "measurement_review": bool(row.get("measurement")),
            }
        )
    fulfillment = raw.get("fulfillment") or {}
    center = fulfillment.get("center") or {} if isinstance(fulfillment, dict) else {}
    code = center.get("code") if isinstance(center, dict) else None
    return {
        "id": string(raw.get("order_id"), 120),
        "shop_id": shop_id,
        "created_at": created.isoformat(),
        "updated_at": updated.isoformat(),
        "status": status,
        "fulfilled_by": None,
        "fulfillment_center": string(code, 120, optional=True),
        "tax_mode": tax_mode,
        "total": monetary(raw.get("total_price"), currency),
        "items": items,
        "within_window": stamp(start) <= updated <= stamp(end),
    }


async def read_account(client, credentials, *, now):
    end = datetime.fromtimestamp(int(now.timestamp()) - 3, UTC).isoformat()
    start = datetime.fromtimestamp(int(now.timestamp()) - 86400, UTC).isoformat()
    if WINDOW:
        start = datetime.fromtimestamp(int(WINDOW["from"]), UTC).isoformat()
        end = datetime.fromtimestamp(int(WINDOW["to"]), UTC).isoformat()
    initial, _ = parameters(credentials, ORDERS, start=start, end=end, offset=0)
    shop = initial["shop_id"]
    cached = identity_cache(credentials, now=int(now.timestamp()))
    orders, seen, totals = [], set(), set()
    has_more, pages_read, offset = False, 0, 0
    while pages_read < MAX_PAGES and offset < LIMIT:
        params, headers = parameters(credentials, ORDERS, start=start, end=end, offset=offset)
        async with client.stream("GET", BASE + ORDERS, params=params, headers=headers) as response:
            status = response.status_code
            if status != 200:
                raise ReadError(
                    "auth_denied"
                    if status in {401, 403}
                    else "rate_limited"
                    if status == 429
                    else "provider_unavailable",
                    http_status=status,
                )
            payload = bytearray()
            async for chunk in response.aiter_bytes():
                payload.extend(chunk)
                if len(payload) > 2 * 1024 * 1024:
                    raise ReadError("invalid_response", "shape", status)
            try:
                body = json.loads(payload, parse_float=Decimal)
            except (ValueError, UnicodeError):
                raise ReadError("invalid_response", "shape", status) from None
        if (
            not isinstance(body, dict)
            or type(body.get("total_count")) is not int
            or not 0 <= body["total_count"] <= 10000000
        ):
            raise ReadError("invalid_response", "shape", 200)
        rows, total = body.get("orders"), body["total_count"]
        if not isinstance(rows, list) or len(rows) > PAGE_SIZE:
            raise ReadError("invalid_response", "shape", 200)
        totals.add(total)
        pages_read += 1
        for raw in rows:
            item = project(raw, shop, start, end)
            if item["id"] in seen:
                raise ReadError("invalid_response", "identifiers", 200)
            seen.add(item["id"])
            orders.append(item)
        has_more = offset + len(rows) < total
        if not rows or not has_more:
            break
        offset += len(rows)
        await asyncio.sleep(0.25)
    coverage_review = (
        len(totals) != 1 or not has_more and len(orders) != total or has_more and not rows
    )
    return {
        "state": "partial_read" if coverage_review else "read_ok",
        "http_status": 200,
        "orders": orders,
        "has_more": has_more,
        "pages_read": pages_read,
        "total_eligible": total,
        "coverage_review": bool(coverage_review),
        "shop_id": shop,
        **cached,
        "date_from": start,
        "date_to": end,
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
    if not 1 <= len(ids) <= 2 or len(set(ids)) != len(ids):
        raise ReadError("configuration_missing")
    report = {
        "version": 1,
        "source": "carrefour_orders_or11_read",
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
                            "WHERE id=ANY(CAST(:ids AS uuid[])) AND platform='carrefour' "
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
            raise ReadError("configuration_missing")
        async with httpx.AsyncClient(timeout=25, follow_redirects=False) as client:
            for row in rows:
                checked = datetime.now(UTC)
                result = {
                    "integration_id": str(row["id"]),
                    "platform": "carrefour",
                    "credentials_revision": row["updated_at"].isoformat(),
                    "checked_at": checked.isoformat(),
                    "state": "provider_unavailable",
                    "http_status": None,
                    "orders": [],
                    "has_more": None,
                    "pages_read": 0,
                    "total_eligible": None,
                    "coverage_review": False,
                    "identity_match": None,
                    "shop_id": None,
                    "identity_basis": None,
                    "identity_checked_at": None,
                    "date_from": None,
                    "date_to": None,
                    "failure_stage": None,
                }
                try:
                    try:
                        credentials = decrypt_json(row["credentials"])
                    except Exception:
                        raise ReadError("credential_unreadable") from None
                    async with asyncio.timeout(90):
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
