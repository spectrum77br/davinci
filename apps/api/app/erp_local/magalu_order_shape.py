"""One Magalu order-schema sample per account; no order values or buyer data.

The public Orders reference currently renders no request/response fields. This
fixed read confirms types before implementing the native projection. It never
refreshes OAuth or returns arbitrary keys, identifiers, dates or error text.
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
FIELDS = {
    "id",
    "code",
    "status",
    "created_at",
    "updated_at",
    "purchased_at",
    "items",
    "order_items",
    "deliveries",
    "payments",
    "amount",
    "amounts",
    "total",
    "total_amount",
    "quantity",
    "qty",
    "seller_sku",
    "sku",
    "sku_seller",
    "sku_id",
    "unit_price",
    "price",
    "prices",
    "normalizer",
    "value",
    "currency",
    "currency_id",
    "product",
    "products",
    "external_id",
    "unit",
    "unitary",
    "discount",
    "freight",
    "unit_amount",
    "total_amounts",
    "seller",
    "channel",
    "name",
    "type",
    "info",
    "sequential",
    "product_id",
    "item_id",
    "seller_id",
    "sku_code",
    "product_info",
    "reference",
    "seller_product_id",
    "external_sku",
    "links",
    "next",
    "previous",
    "self",
    "page",
    "count",
    "limit",
    "max_limit",
    "offset",
}
STATES = {
    "shape_observed",
    "empty_sample",
    "auth_denied",
    "rate_limited",
    "provider_unavailable",
    "invalid_response",
    "credentials_missing",
    "credential_unreadable",
}


def shape(value, key="", depth=0):
    if depth > 7:
        return {"type": "truncated"}
    if isinstance(value, dict):
        keys = sorted(set(value) & FIELDS)
        return {
            "type": "object",
            "fields": {k: shape(value[k], k, depth + 1) for k in keys},
            "unprojected_fields": len(value) - len(keys),
        }
    if isinstance(value, list):
        return {
            "type": "array",
            "length": len(value),
            "first": shape(value[0], key, depth + 1) if value else None,
        }
    result = {
        "type": "null"
        if value is None
        else "boolean"
        if type(value) is bool
        else "integer"
        if type(value) is int
        else "decimal"
        if isinstance(value, Decimal)
        else "string"
        if isinstance(value, str)
        else "unsupported"
    }
    if isinstance(value, str):
        result["format"] = (
            "timestamp"
            if re.fullmatch(r"\d{4}-\d\d-\d\dT[0-9:.]+(?:Z|[+-]\d\d:\d\d)", value)
            else "uuid"
            if re.fullmatch(r"[0-9a-fA-F-]{36}", value)
            else "decimal_string"
            if re.fullmatch(r"-?\d+(?:\.\d+)?", value)
            else "text"
        )
    if key == "normalizer" and type(value) is int and 1 <= value <= 1000000:
        result["value"] = value
    if key in {"currency", "currency_id"} and value == "BRL":
        result["value"] = "BRL"
    if (
        key in {"count", "limit", "max_limit", "offset"}
        and type(value) is int
        and 0 <= value <= 10000000
    ):
        result["value"] = value
    return result


def validate_shape(node, key="", depth=0):
    if not isinstance(node, dict) or depth > 8:
        raise ValueError("shape_projection")
    kind = node.get("type")
    if kind == "object":
        if (
            set(node) != {"type", "fields", "unprojected_fields"}
            or not isinstance(node["fields"], dict)
            or not set(node["fields"]) <= FIELDS
            or type(node["unprojected_fields"]) is not int
            or node["unprojected_fields"] < 0
        ):
            raise ValueError("shape_projection")
        for k, v in node["fields"].items():
            validate_shape(v, k, depth + 1)
    elif kind == "array":
        if (
            set(node) != {"type", "length", "first"}
            or type(node["length"]) is not int
            or node["length"] < 0
            or (node["length"] == 0) != (node["first"] is None)
        ):
            raise ValueError("shape_projection")
        if node["first"] is not None:
            validate_shape(node["first"], key, depth + 1)
    else:
        if kind not in {
            "truncated",
            "null",
            "boolean",
            "integer",
            "decimal",
            "string",
            "unsupported",
        } or set(node) - {"type", "format", "value"}:
            raise ValueError("shape_projection")
        if (kind == "string") != ("format" in node) or node.get("format") not in {
            None,
            "timestamp",
            "uuid",
            "decimal_string",
            "text",
        }:
            raise ValueError("shape_projection")
        if "value" in node and not (
            key == "normalizer"
            and type(node["value"]) is int
            and 1 <= node["value"] <= 1000000
            or key in {"currency", "currency_id"}
            and node["value"] == "BRL"
            or key in {"count", "limit", "max_limit", "offset"}
            and type(node["value"]) is int
            and 0 <= node["value"] <= 10000000
        ):
            raise ValueError("shape_projection")


async def read_account(client, credentials):
    token = credentials.get("access_token") if isinstance(credentials, dict) else None
    if (
        not isinstance(token, str)
        or not token
        or len(token) > 16384
        or any(ord(c) < 32 for c in token)
    ):
        return {
            "state": "credentials_missing",
            "http_status": None,
            "sample_count": 0,
            "has_more": None,
            "order_shape": None,
            "meta_shape": None,
        }
    async with client.stream(
        "GET",
        URL,
        params={"_limit": 1, "_offset": 0},
        headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
    ) as response:
        status = response.status_code
        result = {
            "state": "invalid_response",
            "http_status": status,
            "sample_count": 0,
            "has_more": None,
            "order_shape": None,
            "meta_shape": None,
        }
        if status != 200:
            return {
                **result,
                "state": "auth_denied"
                if status in {401, 403}
                else "rate_limited"
                if status == 429
                else "provider_unavailable",
            }
        payload = bytearray()
        async for chunk in response.aiter_bytes():
            payload.extend(chunk)
            if len(payload) > 2 * 1024 * 1024:
                return result
        try:
            body = json.loads(payload, parse_float=Decimal)
        except (ValueError, UnicodeError):
            return result
    if (
        not isinstance(body, dict)
        or not isinstance(body.get("results"), list)
        or len(body["results"]) > 1
    ):
        return result
    meta = body.get("meta") or {}
    links = meta.get("links") or {} if isinstance(meta, dict) else {}
    more = bool(links.get("next")) if isinstance(links, dict) else None
    rows = body["results"]
    return {
        **result,
        "state": "shape_observed" if rows else "empty_sample",
        "sample_count": len(rows),
        "has_more": more,
        "order_shape": shape(rows[0]) if rows else None,
        "meta_shape": shape(meta),
    }


async def collect(request):
    import httpx
    from sqlalchemy import text

    from app.config import get_settings
    from app.db import SessionLocal, engine
    from app.security.cipher import decrypt_json

    ids = [str(UUID(i)) for i in request["integration_ids"]]
    if not 1 <= len(ids) <= 3 or len(set(ids)) != len(ids):
        raise ValueError("integration_selection")
    report = {
        "version": 2,
        "source": "magalu_order_shape_read",
        "read_only": "on",
        "no_token_refresh": True,
        "external_business_writes": False,
        "sample_limit": 1,
        "window": "not_requested",
        "captured_at": None,
        "accounts": [],
    }
    logging.getLogger("httpx").setLevel(logging.CRITICAL)
    logging.getLogger("httpcore").setLevel(logging.CRITICAL)
    try:
        async with SessionLocal() as session:
            await session.execute(text("SET TRANSACTION READ ONLY"))
            if (await session.execute(text("SHOW transaction_read_only"))).scalar() != "on":
                raise ValueError("read_only_required")
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
            raise ValueError("integration_selection")
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
                    "sample_count": 0,
                    "has_more": None,
                    "order_shape": None,
                    "meta_shape": None,
                }
                try:
                    try:
                        credentials = decrypt_json(row["credentials"])
                    except Exception:
                        result.update(state="credential_unreadable")
                    else:
                        result.update(await read_account(client, credentials))
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
