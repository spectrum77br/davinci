"""Bounded Orders GET projection through the existing server proxy.

The list reference currently omits its date-filter schema. Do not invent a
window or follow returned links. Read a fixed, explicitly unfiltered sample;
keep delivery identity, seller SKU, explicit quantity and monetary normalizer.
No OAuth renewal, database writes, customer data or arbitrary error output.
"""

import asyncio
import base64
import json
import logging
import re
import sys
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

URL = "https://api.magalu.com/seller/v1/orders"
PAGE_SIZE, MAX_PAGES = 50, 4
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
    try:
        return str(UUID(string(value, 36)))
    except (ValueError, TypeError, AttributeError):
        raise ReadError("invalid_response", "identifiers") from None


def stamp(value):
    try:
        date = datetime.fromisoformat(string(value, 64).replace("Z", "+00:00"))
        if date.tzinfo is None or date.utcoffset() is None or value.endswith("-00:00"):
            raise ValueError
        return date.astimezone(UTC)
    except (ValueError, TypeError, AttributeError):
        raise ReadError("invalid_response", "dates") from None


def status(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
        raise ReadError("invalid_response", "fulfillment")
    return value


def monetary(raw, key):
    if raw is None:
        return None
    if (
        not isinstance(raw, dict)
        or type(raw.get(key)) is not int
        or not 0 <= raw[key] <= 999999999000000
        or type(raw.get("normalizer")) is not int
        or raw["normalizer"] not in {1, 10, 100, 1000, 10000, 100000, 1000000}
        or not isinstance(raw.get("currency"), str)
        or not re.fullmatch(r"[A-Z]{3}", raw["currency"])
    ):
        raise ReadError("invalid_response", "money")
    value = Decimal(raw[key]) / Decimal(raw["normalizer"])
    if value > Decimal("999999999"):
        raise ReadError("invalid_response", "money")
    return {"amount": format(value, "f"), "currency": raw["currency"]}


def project(raw):
    if not isinstance(raw, dict):
        raise ReadError("invalid_response", "shape")
    created, updated = stamp(raw.get("created_at")), stamp(raw.get("updated_at"))
    if created > updated:
        raise ReadError("invalid_response", "dates")
    deliveries = raw.get("deliveries")
    if not isinstance(deliveries, list) or not 1 <= len(deliveries) <= 200:
        raise ReadError("invalid_response", "items")
    items, seen = [], set()
    for delivery in deliveries:
        if not isinstance(delivery, dict):
            raise ReadError("invalid_response", "items")
        did = identifier(delivery.get("id"))
        if did in seen:
            raise ReadError("invalid_response", "identifiers")
        seen.add(did)
        rows = delivery.get("items")
        if not isinstance(rows, list) or not 1 <= len(rows) <= 200:
            raise ReadError("invalid_response", "items")
        for index, row in enumerate(rows):
            if not isinstance(row, dict) or not isinstance(row.get("info"), dict):
                raise ReadError("invalid_response", "items")
            quantity = row.get("quantity")
            if type(quantity) is not int or not 0 <= quantity <= 1000000:
                raise ReadError("invalid_response", "quantity")
            sku = string(row["info"].get("sku"))
            items.append(
                {
                    "line_id": f"{did}:{index}",
                    "delivery_id": did,
                    "delivery_status": status(delivery.get("status")),
                    "product_id": identifier(row["info"].get("id")),
                    "listing_id": sku,
                    "variation_id": None,
                    "seller_sku": sku,
                    "quantity": quantity,
                    "unit_price": monetary(row.get("unit_price"), "value"),
                    "total": monetary(row.get("amounts"), "total"),
                }
            )
            if len(items) > 200:
                raise ReadError("invalid_response", "items")
    return {
        "id": string(raw.get("code"), 120),
        "platform_id": identifier(raw.get("id")),
        "created_at": created.isoformat(),
        "updated_at": updated.isoformat(),
        "status": status(raw.get("status")),
        "fulfilled_by": None,
        "total": monetary(raw.get("amounts"), "total"),
        "items": items,
        "within_window": None,
    }


async def read_account(client, credentials):
    token = credentials.get("access_token") if isinstance(credentials, dict) else None
    if (
        not isinstance(token, str)
        or not token
        or len(token) > 16384
        or any(ord(c) < 32 for c in token)
    ):
        raise ReadError("credentials_missing")
    orders, seen, offset, pages = [], set(), 0, 0
    has_more = False
    while pages < MAX_PAGES and len(orders) < LIMIT:
        async with client.stream(
            "GET",
            URL,
            params={"_limit": PAGE_SIZE, "_offset": offset},
            headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
        ) as response:
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
        if not isinstance(body, dict) or not isinstance(body.get("results"), list):
            raise ReadError("invalid_response", "shape", 200)
        rows, meta = body["results"], body.get("meta")
        if (
            len(rows) > PAGE_SIZE
            or not isinstance(meta, dict)
            or not isinstance(meta.get("links"), dict)
            or meta["links"].get("next") is not None
            and not isinstance(meta["links"]["next"], str)
        ):
            raise ReadError("invalid_response", "pagination", 200)
        # Treat links only as continuation signals; never navigate their URLs.
        # Confirmed on an empty account: the final page omits `next` entirely.
        has_more = bool(meta["links"].get("next"))
        page = meta.get("page")
        if isinstance(page, dict):
            for field, expected in [("count", len(rows)), ("offset", offset)]:
                if page.get(field) is not None and (
                    type(page[field]) is not int or page[field] != expected
                ):
                    raise ReadError("invalid_response", "pagination", 200)
        if not rows and has_more:
            raise ReadError("invalid_response", "pagination", 200)
        for raw in rows:
            order = project(raw)
            if order["id"] in seen:
                raise ReadError("invalid_response", "pagination", 200)
            seen.add(order["id"])
            orders.append(order)
        pages += 1
        if not has_more:
            break
        offset += len(rows)
        await asyncio.sleep(0.25)
    return {
        "state": "read_ok",
        "http_status": 200,
        "orders": orders,
        "has_more": has_more,
        "pages_read": pages,
        "coverage_scope": "bounded_unfiltered",
        "identity_match": None,
        "date_from": None,
        "date_to": None,
        "failure_stage": None,
    }


async def collect(request):
    import httpx
    from sqlalchemy import text

    from app.config import get_settings
    from app.db import SessionLocal, engine
    from app.security.cipher import decrypt_json

    ids = [str(UUID(i)) for i in request["integration_ids"]]
    if not 1 <= len(ids) <= 3 or len(set(ids)) != len(ids):
        raise ReadError("configuration_missing")
    report = {
        "version": 1,
        "source": "magalu_orders_v1_read",
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
                            "WHERE id=ANY(CAST(:ids AS uuid[])) AND platform='magalu' "
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
        async with httpx.AsyncClient(
            timeout=25,
            follow_redirects=False,
            trust_env=False,
            proxy=get_settings().magalu_proxy_url or None,
        ) as client:
            for row in rows:
                result = {
                    "integration_id": str(row["id"]),
                    "platform": "magalu",
                    "credentials_revision": row["updated_at"].isoformat(),
                    "checked_at": datetime.now(UTC).isoformat(),
                    "state": "provider_unavailable",
                    "http_status": None,
                    "orders": [],
                    "has_more": None,
                    "pages_read": 0,
                    "coverage_scope": "bounded_unfiltered",
                    "identity_match": None,
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
                        result.update(await read_account(client, credentials))
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
