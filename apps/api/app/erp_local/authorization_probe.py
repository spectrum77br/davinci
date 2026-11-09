"""One fixed GET per native account. Credentials never leave the API container.

This script is also pure-importable for isolated tests. It deliberately does not
use marketplace clients: their request helpers may refresh/persist OAuth tokens.
No refresh, consent, subscription, stock, order or message endpoint is reachable.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

ENDPOINTS = {
    "ml": ("https://api.mercadolibre.com", "/users/me"),
    "shopee": ("https://partner.shopeemobile.com", "/api/v2/shop/get_shop_info"),
    "amazon": ("https://sellingpartnerapi-na.amazon.com", "/sellers/v1/marketplaceParticipations"),
    "tiktok": ("https://open-api.tiktokglobalshop.com", "/authorization/202309/shops"),
    "magalu": ("https://api.magalu.com", "/seller/v1/portfolios/skus"),
    "carrefour": ("https://marketplace.carrefour.com.br/api", "/account"),
}
STATES = {
    "read_ok",
    "identity_review",
    "identity_mismatch",
    "shop_inactive",
    "auth_denied",
    "rate_limited",
    "provider_unavailable",
    "invalid_response",
    "credentials_missing",
    "configuration_missing",
    "credential_unreadable",
    "not_run",
}


class ProbeInputError(ValueError):
    pass


@dataclass(repr=False)
class ReadPlan:
    platform: str
    url: str
    path: str
    headers: dict = field(repr=False)
    params: dict = field(repr=False)
    expected: str = field(repr=False)


def value(credentials, key, *, required=True):
    result = credentials.get(key)
    if isinstance(result, bool) or not isinstance(result, (str, int)):
        result = ""
    result = str(result).strip()
    if (required and not result) or len(result) > 16384 or any(ord(c) < 32 for c in result):
        raise ProbeInputError("credentials_missing")
    return result


def plan(platform, credentials, *, now, partner_id="", partner_key="", sandbox=False):
    if platform not in ENDPOINTS or not isinstance(credentials, dict):
        raise ProbeInputError("configuration_missing")
    base, path = ENDPOINTS[platform]
    headers, params, expected = {"Accept": "application/json"}, {}, ""
    if platform in {"ml", "magalu"}:
        headers["Authorization"] = "Bearer " + value(credentials, "access_token")
        if platform == "ml":
            expected = value(credentials, "user_id")
            if not expected.isdigit():
                raise ProbeInputError("credentials_missing")
        else:
            params = {"_limit": 1}
    elif platform == "shopee":
        token = value(credentials, "access_token")
        expected = value(credentials, "shop_id")
        pid = value(credentials, "partner_id", required=False) or str(partner_id or "")
        secret = value(credentials, "partner_key", required=False) or str(partner_key or "")
        if not pid.isdigit() or int(pid) <= 0 or not secret:
            raise ProbeInputError("configuration_missing")
        if not expected.isdigit() or int(expected) <= 0:
            raise ProbeInputError("credentials_missing")
        pid, expected = str(int(pid)), str(int(expected))
        signature = hmac.new(
            secret.encode(), f"{pid}{path}{now}{token}{expected}".encode(), hashlib.sha256
        ).hexdigest()
        params = {
            "partner_id": pid,
            "timestamp": now,
            "access_token": token,
            "shop_id": expected,
            "sign": signature,
        }
        if sandbox:
            base = "https://partner.test-stable.shopeemobile.com"
    elif platform == "amazon":
        headers["x-amz-access-token"] = value(credentials, "access_token")
        expected = value(credentials, "marketplace_id")
        # This bounded Brazilian operation does not guess a regional endpoint.
        if expected != "A2Q3Y263D00KWC":
            raise ProbeInputError("configuration_missing")
    elif platform == "tiktok":
        headers["x-tts-access-token"] = value(credentials, "access_token")
        headers["Content-Type"] = "application/json"
        key, secret = value(credentials, "app_key"), value(credentials, "app_secret")
        expected = value(credentials, "shop_cipher")
        params = {"app_key": key, "timestamp": str(now)}
        signature_base = path + "".join(k + str(params[k]) for k in sorted(params))
        params["sign"] = hmac.new(
            secret.encode(), (secret + signature_base + secret).encode(), hashlib.sha256
        ).hexdigest()
    elif platform == "carrefour":
        headers["Authorization"] = value(credentials, "api_key")
        expected = value(credentials, "shop_id")
        if not expected.isdigit() or int(expected) <= 0:
            raise ProbeInputError("credentials_missing")
        expected = str(int(expected))
    return ReadPlan(platform, base + path, path, headers, params, expected)


def verdict(read_plan, status, body):
    """Emit fixed classifications only, never remote text, identities or URLs."""
    result = {
        "state": "invalid_response",
        "http_status": status,
        "authorization_valid": False,
        "identity_match": None,
    }
    if status in {401, 403}:
        return {**result, "state": "auth_denied"}
    if status == 429:
        return {**result, "state": "rate_limited"}
    if status >= 500 or 300 <= status < 400:
        return {**result, "state": "provider_unavailable"}
    if status != 200 or not isinstance(body, dict):
        return result
    platform = read_plan.platform
    if platform == "shopee" and body.get("error"):
        state = (
            "auth_denied"
            if body["error"]
            in {
                "error_auth",
                "error_token_invalid",
                "error_token_expired",
                "error_permission",
                "error_sign",
                "invalid_acceess_token",
                "invalid_access_token",
            }
            else "invalid_response"
        )
        return {**result, "state": state}
    if platform == "tiktok" and (type(body.get("code")) is not int or body["code"] != 0):
        return {
            **result,
            "state": "auth_denied"
            if body.get("code")
            in {
                105002,
                36009005,
                105005,
                105006,
            }
            else "invalid_response",
        }
    matched = None
    if platform == "ml":
        if not isinstance(body.get("id"), (str, int)) or isinstance(body["id"], bool):
            return result
        matched = str(body["id"]) == read_plan.expected
    elif platform == "shopee":
        # The endpoint need not echo shop_id. A signed shop request alone does
        # not independently reconcile our saved identity with Seller Center.
        data = body.get("response", body)
        if not isinstance(data, dict) or not isinstance(data.get("shop_name"), str):
            return result
        if data.get("shop_id") is not None:
            matched = str(data["shop_id"]) == read_plan.expected
    elif platform == "tiktok":
        data = body.get("data")
        shops = data.get("shops") if isinstance(data, dict) else None
        if not isinstance(shops, list) or any(not isinstance(s, dict) for s in shops):
            return result
        matched = any(s.get("cipher") == read_plan.expected for s in shops)
    elif platform == "amazon":
        rows = body.get("payload")
        if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
            return result
        # Participation proves the marketplace, not the seller_id saved locally.
        if not any(
            isinstance(r.get("marketplace"), dict)
            and r["marketplace"].get("id") == read_plan.expected
            for r in rows
        ):
            return {**result, "state": "identity_mismatch", "authorization_valid": True}
    elif platform == "magalu":
        if not isinstance(body.get("results"), list):
            return result
    elif platform == "carrefour":
        if not isinstance(body.get("shop_id"), (str, int)) or isinstance(body["shop_id"], bool):
            return result
        matched = str(body["shop_id"]) == read_plan.expected
        if matched and body.get("shop_state") in {"CLOSE", "SUSPENDED", "TERMINATED"}:
            return {
                **result,
                "state": "shop_inactive",
                "authorization_valid": True,
                "identity_match": True,
            }
        if body.get("shop_state") != "OPEN":
            return result
    return {
        **result,
        "state": "read_ok"
        if matched is True
        else "identity_mismatch"
        if matched is False
        else "identity_review",
        "authorization_valid": True,
        "identity_match": matched,
    }


async def execute(read_plan, client):
    """Only this function performs provider IO; GET and targets are fixed."""
    if read_plan.platform not in ENDPOINTS:
        raise ProbeInputError("configuration_missing")
    base, path = ENDPOINTS[read_plan.platform]
    allowed = {base + path}
    if read_plan.platform == "shopee":
        allowed.add("https://partner.test-stable.shopeemobile.com" + path)
    if read_plan.url not in allowed or read_plan.path != path:
        raise ProbeInputError("configuration_missing")
    try:
        async with client.stream(
            "GET", read_plan.url, params=read_plan.params, headers=read_plan.headers
        ) as response:
            payload = bytearray()
            async for chunk in response.aiter_bytes():
                payload.extend(chunk)
                if len(payload) > 1024 * 1024:
                    return verdict(read_plan, response.status_code, None)
            try:
                body = json.loads(payload)
            except (ValueError, UnicodeError):
                body = None
            return verdict(read_plan, response.status_code, body)
    except Exception:
        # Exception strings can contain signed URLs, so none are emitted.
        return {
            "state": "provider_unavailable",
            "http_status": None,
            "authorization_valid": False,
            "identity_match": None,
        }


async def collect(request):
    import httpx
    from sqlalchemy import text

    from app.config import get_settings
    from app.db import SessionLocal, engine
    from app.security.cipher import decrypt_json

    ids = [str(UUID(i)) for i in request["integration_ids"]]
    if not ids or len(ids) > 64 or len(set(ids)) != len(ids):
        raise ProbeInputError("configuration_missing")
    report = {
        "version": 1,
        "source": "davinci_native_authorization_read",
        "read_only": "on",
        "no_token_refresh": True,
        "external_business_writes": False,
        "captured_at": None,
        "accounts": [],
    }
    logging.getLogger("httpx").setLevel(logging.CRITICAL)
    logging.getLogger("httpcore").setLevel(logging.CRITICAL)
    try:
        async with SessionLocal() as session:
            await session.execute(text("SET TRANSACTION READ ONLY"))
            if (await session.execute(text("SHOW transaction_read_only"))).scalar() != "on":
                raise ProbeInputError("configuration_missing")
            rows = (
                (
                    await session.execute(
                        text(
                            "SELECT id,platform::text AS platform,credentials,updated_at "
                            "FROM davinci.integrations WHERE id = ANY(CAST(:ids AS uuid[])) "
                            "AND status='active' AND archived_at IS NULL ORDER BY platform,id"
                        ),
                        {"ids": ids},
                    )
                )
                .mappings()
                .all()
            )
            await session.rollback()
        settings = get_settings()
        started = time.monotonic()
        for row in rows:
            platform = row["platform"]
            if platform not in ENDPOINTS:
                continue
            result = {
                "integration_id": str(row["id"]),
                "platform": platform,
                "credentials_revision": row["updated_at"].isoformat(),
                "checked_at": datetime.now(UTC).isoformat(),
                "state": "not_run",
                "http_status": None,
                "authorization_valid": False,
                "identity_match": None,
            }
            if time.monotonic() - started > 240:
                report["accounts"].append(result)
                continue
            try:
                credentials = decrypt_json(row["credentials"])
            except Exception:
                result["state"] = "credential_unreadable"
                report["accounts"].append(result)
                continue
            try:
                read_plan = plan(
                    platform,
                    credentials,
                    now=int(time.time()),
                    partner_id=settings.shopee_partner_id,
                    partner_key=settings.shopee_partner_key,
                    sandbox=settings.shopee_use_sandbox,
                )
            except ProbeInputError as exc:
                result["state"] = str(exc)
            else:
                proxy = (settings.magalu_proxy_url or None) if platform == "magalu" else None
                async with httpx.AsyncClient(
                    timeout=15, follow_redirects=False, trust_env=False, proxy=proxy
                ) as client:
                    result.update(await execute(read_plan, client))
                await asyncio.sleep(1.2)
            report["accounts"].append(result)
    finally:
        report["captured_at"] = datetime.now(UTC).isoformat()
        await engine.dispose()
    return report


if __name__ == "__main__":
    try:
        request = json.loads(base64.b64decode(sys.argv[1], validate=True))
        print(json.dumps(asyncio.run(collect(request)), ensure_ascii=False))
    except Exception:
        # Database/config/transport failures must not print credentials or SQL.
        print(json.dumps({"error": "authorization_read_unavailable"}))
        sys.exit(1)
