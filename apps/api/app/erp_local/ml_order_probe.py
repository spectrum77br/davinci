"""Bounded native Mercado Livre GET reader; identity before order search.

Only /users/me and /orders/search are reachable. Credentials remain on the
server; no token refresh, messages, labels, payment or order mutations.
Primary contract: developers.mercadolivre.com.br/pt_br/gerenciamento-de-vendas.
"""

import asyncio
import base64
import json
import logging
import re
import sys
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID

WINDOW = None
BASE = "https://api.mercadolibre.com"
PAGE_SIZE, MAX_PAGES = 50, 10
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


def identifier(value, optional=False):
    if optional and value is None:
        return None
    if type(value) not in {str, int} or not re.fullmatch(r"[1-9]\d{0,19}", str(value)):
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
        if type(value) not in {str, int, Decimal} or len(str(value)) > 64:
            raise ValueError
        amount = Decimal(str(value))
        if (
            not amount.is_finite()
            or not 0 <= amount <= Decimal("999999999")
            or not isinstance(currency, str)
            or not re.fullmatch(r"[A-Z]{3}", currency)
        ):
            raise ValueError
    except (ValueError, TypeError, InvalidOperation):
        raise ReadError("invalid_response", "money") from None
    return {"amount": format(amount, "f"), "currency": currency}


def status(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z_]{1,64}", value):
        raise ReadError("invalid_response", "fulfillment")
    return value


def project(raw, seller_id, start, end):
    if not isinstance(raw, dict):
        raise ReadError("invalid_response", "shape")
    seller = raw.get("seller")
    if not isinstance(seller, dict) or identifier(seller.get("id")) != seller_id:
        raise ReadError("identity_mismatch", "seller")
    created, updated = stamp(raw.get("date_created")), stamp(raw.get("date_last_updated"))
    if created > updated:
        raise ReadError("invalid_response", "dates")
    rows, currency = raw.get("order_items"), raw.get("currency_id")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 200:
        raise ReadError("invalid_response", "items")
    items = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or not isinstance(row.get("item"), dict):
            raise ReadError("invalid_response", "items")
        item, quantity = row["item"], row.get("quantity")
        listing = string(item.get("id"), 32)
        if not re.fullmatch(r"MLB[1-9]\d{0,19}", listing):
            raise ReadError("invalid_response", "identifiers")
        if type(quantity) is not int or not 0 <= quantity <= 1000000:
            raise ReadError("invalid_response", "quantity")
        items.append(
            {
                "line_id": str(index),
                "listing_id": listing,
                "variation_id": identifier(item.get("variation_id"), optional=True),
                "seller_sku": string(item.get("seller_sku"), optional=True),
                "seller_custom_field": string(item.get("seller_custom_field"), optional=True),
                "quantity": quantity,
                "unit_price": monetary(row.get("unit_price"), row.get("currency_id") or currency),
            }
        )
    shipping = raw.get("shipping")
    if shipping is not None and not isinstance(shipping, dict):
        raise ReadError("invalid_response", "shape")
    return {
        "id": identifier(raw.get("id")),
        "seller_id": seller_id,
        "pack_id": identifier(raw.get("pack_id"), optional=True),
        "shipping_id": identifier((shipping or {}).get("id"), optional=True),
        "created_at": created.isoformat(),
        "updated_at": updated.isoformat(),
        "status": status(raw.get("status")),
        "fulfilled_by": None,
        "total": monetary(raw.get("total_amount"), currency),
        # What the buyer paid, shipping included: Bling keeps this as the order total.
        "paid": monetary(raw.get("paid_amount"), currency),
        "items": items,
        "within_window": stamp(start) <= updated <= stamp(end),
    }


async def get(client, path, headers, params=None):
    if path not in {"/users/me", "/orders/search"}:
        raise ReadError("configuration_missing")
    async with client.stream("GET", BASE + path, headers=headers, params=params) as response:
        code = response.status_code
        if code != 200:
            raise ReadError(
                "auth_denied"
                if code in {401, 403}
                else "rate_limited"
                if code == 429
                else "provider_unavailable",
                http_status=code,
            )
        payload = bytearray()
        async for chunk in response.aiter_bytes():
            payload.extend(chunk)
            if len(payload) > 2 * 1024 * 1024:
                raise ReadError("invalid_response", "shape", code)
        try:
            body = json.loads(payload, parse_float=Decimal)
        except (ValueError, UnicodeError):
            raise ReadError("invalid_response", "shape", code) from None
    if not isinstance(body, dict):
        raise ReadError("invalid_response", "shape", 200)
    return body


async def read_account(client, credentials, *, now):
    try:
        token = string(credentials.get("access_token"), 16384)
        seller = identifier(credentials.get("user_id"))
    except (ReadError, AttributeError):
        raise ReadError("credentials_missing") from None
    headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
    me = await get(client, "/users/me", headers)
    if identifier(me.get("id")) != seller or me.get("site_id") != "MLB":
        raise ReadError("identity_mismatch", "seller", 200)
    start = datetime.fromtimestamp(int(now.timestamp()) - 86400, UTC).isoformat()
    end = datetime.fromtimestamp(int(now.timestamp()) - 3, UTC).isoformat()
    if WINDOW:
        start = datetime.fromtimestamp(int(WINDOW["from"]), UTC).isoformat()
        end = datetime.fromtimestamp(int(WINDOW["to"]), UTC).isoformat()
    orders, seen, totals, offset, pages = [], set(), set(), 0, 0
    more = False
    while pages < MAX_PAGES and offset < LIMIT:
        body = await get(
            client,
            "/orders/search",
            headers,
            {
                "seller": seller,
                "order.date_last_updated.from": start,
                "order.date_last_updated.to": end,
                "sort": "date_desc",
                "limit": PAGE_SIZE,
                "offset": offset,
            },
        )
        rows, paging = body.get("results"), body.get("paging")
        if (
            not isinstance(rows, list)
            or len(rows) > PAGE_SIZE
            or not isinstance(paging, dict)
            or type(paging.get("total")) is not int
            or not 0 <= paging["total"] <= 10000000
            or type(paging.get("offset")) is not int
            or paging["offset"] != offset
            or type(paging.get("limit")) is not int
            or not 1 <= paging["limit"] <= PAGE_SIZE
        ):
            raise ReadError("invalid_response", "pagination", 200)
        total = paging["total"]
        totals.add(total)
        for raw in rows:
            order = project(raw, seller, start, end)
            if order["id"] in seen:
                raise ReadError("invalid_response", "pagination", 200)
            seen.add(order["id"])
            orders.append(order)
        pages += 1
        more = offset + len(rows) < total
        if not more or not rows:
            break
        offset += len(rows)
        await asyncio.sleep(0.25)
    review = (
        len(totals) != 1
        or (not more and len(orders) != total)
        or (more and not rows)
        or any(not o["within_window"] for o in orders)
    )
    return {
        "state": "partial_read" if review else "read_ok",
        "http_status": 200,
        "orders": orders,
        "has_more": more,
        "pages_read": pages,
        "total_eligible": total,
        "coverage_review": bool(review),
        "identity_match": True,
        "seller_id": seller,
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
    if not 1 <= len(ids) <= 24 or len(set(ids)) != len(ids):
        raise ReadError("configuration_missing")
    report = {
        "version": 1,
        "source": "ml_orders_v1_read",
        "read_only": "on",
        "no_token_refresh": True,
        "external_business_writes": False,
        "limit_per_account": LIMIT,
        "captured_at": None,
        "accounts": [],
    }
    logging.getLogger("httpx").setLevel(logging.CRITICAL)
    logging.getLogger("httpcore").setLevel(logging.CRITICAL)
    deadline = asyncio.get_running_loop().time() + 210
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
                            "WHERE id=ANY(CAST(:ids AS uuid[])) AND platform='ml' "
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
        async with httpx.AsyncClient(timeout=20, follow_redirects=False, trust_env=False) as client:
            for row in rows:
                checked = datetime.now(UTC)
                result = {
                    "integration_id": str(row["id"]),
                    "platform": "ml",
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
                    "seller_id": None,
                    "date_from": None,
                    "date_to": None,
                    "failure_stage": None,
                }
                budget = min(45, deadline - asyncio.get_running_loop().time())
                if budget > 1:
                    try:
                        try:
                            credentials = decrypt_json(row["credentials"])
                        except Exception:
                            raise ReadError("credential_unreadable") from None
                        async with asyncio.timeout(budget):
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
