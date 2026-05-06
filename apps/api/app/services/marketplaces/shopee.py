"""Shopee Open Platform v2 client (Fase 4b.Shopee).

Implements `MarketplaceClient` for Shopee. Resolves bug B5 — distinguish 403
"product banned" from real auth errors and map every Shopee error code into
the right `LinkSyncStatus` so the UI can route alerts correctly.

Auth model: Shopee Open Platform uses partner-signed requests. Every call
carries `partner_id`, `timestamp`, and HMAC-SHA256 signature. Tokens are
shop-scoped and refreshed via /auth/access_token/get.

Credentials shape stored in `integrations.credentials`:
    {
      "shop_id":       int,
      "access_token":  str,
      "refresh_token": str,
      "expires_at":    int (epoch seconds),
    }

Stock update endpoint: POST /api/v2/product/update_stock
  body: {"item_id": int, "stock_list": [{"model_id": int, "normal_stock": int}]}
For listings without variations, `model_id=0` writes to the master.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx
import structlog

from app.config import get_settings
from app.services.marketplaces.base import SyncResult, SyncStatus, TestResult

if TYPE_CHECKING:
    from app.models import ProductLink

logger = structlog.get_logger()

SHOPEE_LIVE_BASE = "https://partner.shopeemobile.com"
SHOPEE_TEST_BASE = "https://partner.test-stable.shopeemobile.com"


# Error codes that mean the listing has been removed/banned/suspended by
# Shopee (operational state) — not a transient or auth problem. These map to
# REQUIRES_REVIEW so a human routes them to the "produto banido" channel.
_BANNED_CODES: frozenset[str] = frozenset(
    {
        "error_item_deleted",
        "error_item_banned",
        "error_item_unlist",
        "product.error_status",
        "error_listing_banned",
        "error_item_off_shelf",
    }
)

# Error codes that mean Shopee rejected our token (refresh/reconnect needed).
_AUTH_CODES: frozenset[str] = frozenset(
    {
        "error_auth",
        "error_token_invalid",
        "error_token_expired",
        "error_permission",
    }
)


class ShopeeClient:
    def __init__(self, creds: dict, on_token_refresh=None):
        self.creds = dict(creds)
        self._on_refresh = on_token_refresh

    @property
    def access_token(self) -> str:
        return str(self.creds.get("access_token") or "")

    @property
    def shop_id(self) -> int:
        return int(self.creds.get("shop_id") or 0)

    @property
    def expires_at(self) -> int:
        return int(self.creds.get("expires_at") or 0)

    def _expired(self, skew: int = 30) -> bool:
        return self.expires_at - skew <= int(time.time())

    @property
    def _base(self) -> str:
        s = get_settings()
        return SHOPEE_TEST_BASE if s.shopee_use_sandbox else SHOPEE_LIVE_BASE

    def _sign(self, path: str, timestamp: int) -> tuple[str, int]:
        """Returns (signature, partner_id) for the given path. Signed shape:
        partner_id|path|timestamp|access_token|shop_id  (HMAC-SHA256 hex)."""
        s = get_settings()
        partner_id = int(s.shopee_partner_id or 0)
        partner_key = (s.shopee_partner_key or "").encode()
        msg = f"{partner_id}{path}{timestamp}{self.access_token}{self.shop_id}".encode()
        sig = hmac.new(partner_key, msg, hashlib.sha256).hexdigest()
        return sig, partner_id

    async def refresh(self) -> None:
        rt = self.creds.get("refresh_token")
        if not rt:
            raise RuntimeError("missing refresh_token")
        path = "/api/v2/auth/access_token/get"
        ts = int(time.time())
        s = get_settings()
        partner_id = int(s.shopee_partner_id or 0)
        partner_key = (s.shopee_partner_key or "").encode()
        # Refresh sign uses partner_id|path|timestamp (no token, no shop_id).
        msg = f"{partner_id}{path}{ts}".encode()
        sig = hmac.new(partner_key, msg, hashlib.sha256).hexdigest()
        params = {"partner_id": partner_id, "timestamp": ts, "sign": sig}
        async with httpx.AsyncClient(timeout=20.0) as c:
            r = await c.post(
                f"{self._base}{path}",
                params=params,
                json={
                    "refresh_token": rt,
                    "shop_id": self.shop_id,
                    "partner_id": partner_id,
                },
            )
            r.raise_for_status()
            payload = r.json() or {}
        self.creds["access_token"] = payload["access_token"]
        if "refresh_token" in payload:
            self.creds["refresh_token"] = payload["refresh_token"]
        expires_in = int(payload.get("expire_in") or 14400)
        self.creds["expires_at"] = int(time.time()) + expires_in
        self.creds["_obtained_at"] = datetime.now(UTC).isoformat()
        if self._on_refresh:
            await self._on_refresh(self.creds)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json: Any = None,
    ) -> httpx.Response:
        if self._expired():
            await self.refresh()
        ts = int(time.time())
        sig, partner_id = self._sign(path, ts)
        q = {
            "partner_id": partner_id,
            "timestamp": ts,
            "access_token": self.access_token,
            "shop_id": self.shop_id,
            "sign": sig,
        }
        if params:
            q.update(params)
        async with httpx.AsyncClient(timeout=30.0) as c:
            return await c.request(method, f"{self._base}{path}", params=q, json=json)

    async def test_connection(self) -> TestResult:
        try:
            r = await self._request("GET", "/api/v2/shop/get_shop_info")
            if r.status_code == 200:
                body = r.json() or {}
                if not body.get("error"):
                    return TestResult(ok=True, info={"shop_id": self.shop_id, "name": body.get("shop_name")})
                return TestResult(ok=False, detail=f"{body.get('error')}: {body.get('message')}")
            return TestResult(ok=False, detail=f"status={r.status_code} body={r.text[:200]}")
        except httpx.HTTPError as e:
            return TestResult(ok=False, detail=f"http_error: {e}")
        except Exception as e:  # noqa: BLE001
            return TestResult(ok=False, detail=f"error: {e}")

    async def update_stock(
        self,
        link: "ProductLink",
        qty: int,
        *,
        bling_store_id: int | None = None,  # ignored for Shopee
    ) -> SyncResult:
        del bling_store_id
        qty_before = link.stock
        try:
            item_id = int(link.external_id)
        except (TypeError, ValueError):
            return SyncResult(
                status=SyncStatus.FATAL,
                qty_before=qty_before,
                error_code="invalid_external_id",
                error_detail=f"external_id={link.external_id!r}",
            )
        try:
            model_id = int(link.variation_id) if link.variation_id else 0
        except (TypeError, ValueError):
            model_id = 0

        body = {
            "item_id": item_id,
            "stock_list": [{"model_id": model_id, "normal_stock": qty}],
        }
        try:
            r = await self._request("POST", "/api/v2/product/update_stock", json=body)
        except httpx.HTTPError as e:
            return _http_error_to_result(e, qty_before)

        return _classify_response(r, qty_before, qty_after=qty)


# ---------------------------------------------------------------- helpers


def _classify_response(
    r: httpx.Response, qty_before: int | None, *, qty_after: int
) -> SyncResult:
    """Shopee returns 200 even when the request was rejected; the JSON body
    carries `error` + `message`. B5 hinges on reading the body, not the HTTP
    code, before deciding the bucket."""
    if r.status_code in {429, 502, 503, 504}:
        return SyncResult(
            status=SyncStatus.RETRYABLE,
            qty_before=qty_before,
            error_code=f"http_{r.status_code}",
            error_detail=r.text[:500],
        )

    try:
        payload = r.json() or {}
    except ValueError:
        return SyncResult(
            status=SyncStatus.FATAL,
            qty_before=qty_before,
            error_code="shopee_invalid_json",
            error_detail=r.text[:500],
        )

    err = (payload.get("error") or "").strip()
    msg = (payload.get("message") or payload.get("msg") or "").strip()

    if not err:
        return SyncResult(
            status=SyncStatus.OK,
            qty_before=qty_before,
            qty_after=qty_after,
            payload={"request_id": payload.get("request_id")} if payload.get("request_id") else {},
        )

    err_lower = err.lower()
    if err in _BANNED_CODES or any(
        kw in err_lower or kw in msg.lower() for kw in ("ban", "delisted", "removed", "off-shelf", "off_shelf")
    ):
        return SyncResult(
            status=SyncStatus.REQUIRES_REVIEW,
            qty_before=qty_before,
            error_code=err or "shopee_banned",
            error_detail=msg[:500] if msg else None,
            payload={"shopee_classification": "banned"},
        )

    if err in _AUTH_CODES:
        return SyncResult(
            status=SyncStatus.FATAL,
            qty_before=qty_before,
            error_code=err,
            error_detail=msg[:500] if msg else None,
            payload={"shopee_classification": "auth"},
        )

    if err.startswith("error_param") or err.startswith("error_data"):
        return SyncResult(
            status=SyncStatus.FATAL,
            qty_before=qty_before,
            error_code=err,
            error_detail=msg[:500] if msg else None,
        )

    if err.startswith("error_server") or err == "error_inner":
        return SyncResult(
            status=SyncStatus.RETRYABLE,
            qty_before=qty_before,
            error_code=err,
            error_detail=msg[:500] if msg else None,
        )

    # Unknown error code — be conservative: mark RETRYABLE so we don't FATAL
    # and silently lose retries on a code we haven't classified yet.
    return SyncResult(
        status=SyncStatus.RETRYABLE,
        qty_before=qty_before,
        error_code=err or "shopee_unknown_error",
        error_detail=msg[:500] if msg else None,
        payload={"shopee_classification": "unknown"},
    )


def _http_error_to_result(e: httpx.HTTPError, qty_before: int | None) -> SyncResult:
    response = getattr(e, "response", None)
    code = response.status_code if response is not None else None
    if code in {429, 502, 503, 504} or code is None:
        return SyncResult(
            status=SyncStatus.RETRYABLE,
            qty_before=qty_before,
            error_code=f"shopee_http_{code or 'network'}",
            error_detail=str(e)[:500],
        )
    return SyncResult(
        status=SyncStatus.FATAL,
        qty_before=qty_before,
        error_code=f"shopee_http_{code}",
        error_detail=str(e)[:500],
    )
