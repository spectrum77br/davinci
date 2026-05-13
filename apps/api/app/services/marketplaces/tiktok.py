"""TikTok Shop client (full implementation).

Implements `MarketplaceClient` for TikTok Shop using API v202309.
Supports: test_connection, update_stock, update_price, activate_product,
search_products, update_price_with_activation, OAuth (authorize/exchange/
refresh), and lazy access_token refresh before each request.

Auth: HMAC-SHA256 signing per TikTok v202309 spec.
Credentials shape stored in `integrations.credentials`:
    {
      "app_key":                 str,
      "app_secret":              str,
      "service_id":              str,   # filled at integration creation
      "access_token":            str,
      "refresh_token":           str,
      "shop_cipher":             str,
      "shop_name":               str,
      "open_id":                 str,
      "seller_name":             str,
      "seller_base_region":      str,
      "token_expires_at":        int,   # epoch seconds
      "refresh_token_expires_at": int,  # epoch seconds
    }
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import TYPE_CHECKING, Any
from collections.abc import AsyncIterator

import httpx
import structlog

from app.services.marketplaces.base import SyncResult, SyncStatus, TestResult

if TYPE_CHECKING:
    from app.models import ProductLink

logger = structlog.get_logger()

TIKTOK_BASE_URL = "https://open-api.tiktokglobalshop.com"
TIKTOK_AUTH_BASE = "https://auth.tiktok-shops.com"
TIKTOK_AUTHORIZE_BASE = "https://services.tiktokshop.com/open/authorize"
TOKEN_REFRESH_BUFFER_SEC = 300  # refresh if expiring within 5 min


class TikTokClient:
    def __init__(self, creds: dict, on_token_refresh=None):
        self.creds = dict(creds)
        self._on_refresh = on_token_refresh

    @property
    def app_key(self) -> str:
        return str(self.creds.get("app_key") or "").strip()

    @property
    def app_secret(self) -> str:
        return str(self.creds.get("app_secret") or "").strip()

    @property
    def access_token(self) -> str:
        return str(self.creds.get("access_token") or "").strip()

    @property
    def shop_cipher(self) -> str:
        return str(self.creds.get("shop_cipher") or "").strip()

    @property
    def refresh_token_value(self) -> str:
        return str(self.creds.get("refresh_token") or "").strip()

    @property
    def token_expires_at(self) -> int:
        return int(self.creds.get("token_expires_at") or 0)

    # ---------------------------------------------------------------- OAuth (static)

    @staticmethod
    def authorize_url(service_id: str, state: str) -> str:
        """Builds TikTok Partner authorize URL. The TikTok Partner Center
        receives the user, then redirects back to the app's configured
        callback with `?code=...&state=...`.
        """
        if not service_id:
            raise ValueError("service_id is required to build a TikTok authorize URL")
        return (
            f"{TIKTOK_AUTHORIZE_BASE}"
            f"?service_id={service_id}&state={state}"
        )

    @staticmethod
    async def exchange_code(code: str, app_key: str, app_secret: str) -> dict:
        """Exchange an auth code for access+refresh tokens.

        Returns the raw `data` dict from TikTok's response, augmented with
        absolute expiry timestamps.
        """
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.get(
                f"{TIKTOK_AUTH_BASE}/api/v2/token/get",
                params={
                    "app_key": app_key,
                    "app_secret": app_secret,
                    "auth_code": code,
                    "grant_type": "authorized_code",
                },
            )
        r.raise_for_status()
        body = r.json()
        if body.get("code") != 0:
            raise RuntimeError(f"TikTok token/get failed: {body.get('message')}")
        data = body.get("data") or {}
        now = int(time.time())
        data["token_expires_at"] = now + int(data.get("access_token_expire_in") or 0)
        data["refresh_token_expires_at"] = now + int(
            data.get("refresh_token_expire_in") or 0
        )
        return data

    @staticmethod
    async def refresh_access_token(
        refresh_token: str, app_key: str, app_secret: str
    ) -> dict:
        """Use a refresh_token to obtain a new access_token."""
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.get(
                f"{TIKTOK_AUTH_BASE}/api/v2/token/refresh",
                params={
                    "app_key": app_key,
                    "app_secret": app_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
            )
        r.raise_for_status()
        body = r.json()
        if body.get("code") != 0:
            raise RuntimeError(f"TikTok token/refresh failed: {body.get('message')}")
        data = body.get("data") or {}
        now = int(time.time())
        data["token_expires_at"] = now + int(data.get("access_token_expire_in") or 0)
        data["refresh_token_expires_at"] = now + int(
            data.get("refresh_token_expire_in") or 0
        )
        return data

    @staticmethod
    async def fetch_shop_info(
        access_token: str, app_key: str, app_secret: str
    ) -> dict:
        """Fetches the authorized shop record (shop_cipher, shop_name, region).

        Calls `/authorization/202309/shops` *without* a shop_cipher (the
        endpoint that returns the cipher itself). Returns the first shop
        in the response or an empty dict.
        """
        path = "/authorization/202309/shops"
        ts = int(time.time())
        params: dict[str, str] = {"app_key": app_key, "timestamp": str(ts)}
        sign = _sign(app_secret, path, params)
        params["sign"] = sign
        params["access_token"] = access_token
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.get(
                f"{TIKTOK_BASE_URL}{path}",
                params=params,
                headers={
                    "x-tts-access-token": access_token,
                    "content-type": "application/json",
                },
            )
        r.raise_for_status()
        body = r.json()
        if body.get("code") != 0:
            raise RuntimeError(f"TikTok shops fetch failed: {body.get('message')}")
        shops = (body.get("data") or {}).get("shops") or []
        return shops[0] if shops else {}

    # ---------------------------------------------------------------- token freshness

    async def _ensure_fresh_token(self) -> None:
        """Refresh access_token before requests if it's near expiry.

        Skipped when refresh_token or token_expires_at is missing (e.g. legacy
        creds populated by the cutover migration without an absolute expiry).
        """
        exp = self.token_expires_at
        rt = self.refresh_token_value
        if not exp or not rt or not self.app_key or not self.app_secret:
            return
        if exp - int(time.time()) > TOKEN_REFRESH_BUFFER_SEC:
            return
        try:
            data = await TikTokClient.refresh_access_token(
                rt, self.app_key, self.app_secret
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("tiktok_refresh_failed", err=str(e))
            return
        # Merge the new tokens into our creds + notify the caller so it can
        # persist them to the integration row.
        self.creds.update(
            {
                "access_token": data.get("access_token") or self.creds.get("access_token"),
                "refresh_token": data.get("refresh_token") or rt,
                "token_expires_at": data["token_expires_at"],
                "refresh_token_expires_at": data["refresh_token_expires_at"],
            }
        )
        if self._on_refresh is not None:
            try:
                await self._on_refresh(self.creds)
            except Exception as e:  # noqa: BLE001
                logger.warning("tiktok_refresh_callback_failed", err=str(e))

    # ---------------------------------------------------------------- signing

    def _timestamp(self) -> int:
        return int(time.time())

    def _generate_sign(
        self,
        path: str,
        query_params: dict[str, str],
        body: str | None = None,
    ) -> str:
        """HMAC-SHA256 signature per TikTok v202309 spec.

        Steps:
        1. Filter out 'sign' and 'access_token' from query params
        2. Sort remaining params alphabetically by key
        3. Concatenate: path + sorted {key}{value} pairs
        4. Append body if present (POST with JSON body)
        5. Wrap with secret on both sides: secret + string + secret
        6. HMAC-SHA256(secret, wrapped_string) → hex
        """
        filtered = {
            k: v
            for k, v in query_params.items()
            if k not in ("sign", "access_token")
        }
        sorted_keys = sorted(filtered.keys())

        sign_string = path
        for key in sorted_keys:
            sign_string += key + filtered[key]

        if body:
            sign_string += body

        wrapped = self.app_secret + sign_string + self.app_secret
        return hmac.new(
            self.app_secret.encode(), wrapped.encode(), hashlib.sha256
        ).hexdigest()

    def _build_get_params(
        self, path: str, extra_params: dict[str, str] | None = None
    ) -> dict[str, str]:
        """Build query params for a GET request (no body)."""
        ts = self._timestamp()
        params: dict[str, str] = {
            "app_key": self.app_key,
            "timestamp": str(ts),
        }
        if self.shop_cipher:
            params["shop_cipher"] = self.shop_cipher
        if extra_params:
            params.update(extra_params)

        sign = self._generate_sign(path, params)
        return {**params, "sign": sign, "access_token": self.access_token}

    def _build_post_params(
        self,
        path: str,
        body: str,
        extra_params: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """Build query params for a POST request (with body in signature)."""
        ts = self._timestamp()
        params: dict[str, str] = {
            "app_key": self.app_key,
            "timestamp": str(ts),
        }
        if self.shop_cipher:
            params["shop_cipher"] = self.shop_cipher
        if extra_params:
            params.update(extra_params)

        sign = self._generate_sign(path, params, body)
        return {**params, "sign": sign, "access_token": self.access_token}

    # ---------------------------------------------------------------- HTTP helpers

    async def _get(
        self, path: str, extra_params: dict[str, str] | None = None
    ) -> dict:
        await self._ensure_fresh_token()
        params = self._build_get_params(path, extra_params)
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.get(
                f"{TIKTOK_BASE_URL}{path}",
                params=params,
                headers={
                    "x-tts-access-token": self.access_token,
                    "content-type": "application/json",
                },
            )
        if r.status_code == 200:
            return r.json()
        return {"code": r.status_code, "message": r.text[:500]}

    async def _post(
        self,
        path: str,
        body: dict | None = None,
        extra_params: dict[str, str] | None = None,
    ) -> dict:
        await self._ensure_fresh_token()
        body_str = json.dumps(body or {}, separators=(",", ":"), ensure_ascii=False)
        params = self._build_post_params(path, body_str, extra_params)
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.post(
                f"{TIKTOK_BASE_URL}{path}",
                content=body_str,
                params=params,
                headers={
                    "x-tts-access-token": self.access_token,
                    "content-type": "application/json",
                },
            )
        if r.status_code == 200:
            return r.json()
        return {"code": r.status_code, "message": r.text[:500]}

    # ---------------------------------------------------------------- MarketplaceClient interface

    async def test_connection(self) -> TestResult:
        required = ("app_key", "app_secret", "access_token", "shop_cipher")
        missing = [k for k in required if not self.creds.get(k)]
        if missing:
            return TestResult(
                ok=False, detail=f"missing_credentials: {','.join(missing)}"
            )
        try:
            # Get Authorized Shops — validates token
            path = "/authorization/202309/shops"
            ts = self._timestamp()
            params: dict[str, str] = {
                "app_key": self.app_key,
                "timestamp": str(ts),
            }
            sign = self._generate_sign(path, params)
            params["sign"] = sign
            params["access_token"] = self.access_token

            async with httpx.AsyncClient(timeout=15.0) as c:
                r = await c.get(
                    f"{TIKTOK_BASE_URL}{path}",
                    params=params,
                    headers={
                        "x-tts-access-token": self.access_token,
                        "content-type": "application/json",
                    },
                )

            data = r.json() if r.status_code == 200 else {}
            if data.get("code") == 0:
                shops = data.get("data", {}).get("shops", [])
                names = ", ".join(s.get("shop_name", "?") for s in shops)
                return TestResult(
                    ok=True,
                    detail=f"Lojas: {names or 'N/A'}",
                    info={"shops": shops},
                )
            return TestResult(
                ok=False,
                detail=f"TikTok error: {data.get('message', 'Unknown')}",
            )
        except httpx.HTTPError as e:
            return TestResult(ok=False, detail=f"http_error: {e}")
        except Exception as e:  # noqa: BLE001
            return TestResult(ok=False, detail=f"error: {e}")

    async def update_stock(
        self,
        link: "ProductLink",
        qty: int,
        *,
        bling_store_id: int | None = None,
    ) -> SyncResult:
        """Push stock to a TikTok product SKU.

        link.external_id = TikTok product_id
        link.variation_id = TikTok sku_id
        """
        del bling_store_id
        qty_before = link.stock
        product_id = (link.external_id or "").strip()
        sku_id = (link.variation_id or "").strip()

        if not product_id or not sku_id:
            return SyncResult(
                status=SyncStatus.FATAL,
                qty_before=qty_before,
                error_code="tiktok_missing_ids",
                error_detail=f"product_id={product_id!r}, sku_id={sku_id!r}",
            )

        try:
            await self._update_product_stock(product_id, sku_id, qty)
            return SyncResult(
                status=SyncStatus.OK,
                qty_before=qty_before,
                qty_after=qty,
                payload={"product_id": product_id, "sku_id": sku_id},
            )
        except httpx.HTTPError as e:
            return _http_error_to_result(e, qty_before)
        except Exception as e:  # noqa: BLE001
            return SyncResult(
                status=SyncStatus.FATAL,
                qty_before=qty_before,
                error_code="tiktok_exception",
                error_detail=str(e)[:500],
            )

    # ---------------------------------------------------------------- stock operations

    async def _update_product_stock(
        self, product_id: str, sku_id: str, quantity: int, warehouse_id: str | None = None
    ) -> bool:
        """Update inventory for a single SKU within a product."""
        path = f"/product/202309/products/{product_id}/inventory/update"
        inventory_item: dict[str, Any] = {"quantity": quantity}
        if warehouse_id:
            inventory_item["warehouse_id"] = warehouse_id

        body = {"skus": [{"id": sku_id, "inventory": [inventory_item]}]}
        resp = await self._post(path, body)

        if resp.get("code") != 0:
            msg = resp.get("message", "Unknown error")
            logger.error(
                "tiktok_update_stock_failed",
                product_id=product_id,
                sku_id=sku_id,
                code=resp.get("code"),
                message=msg,
            )
            raise RuntimeError(f"TikTok API error: {msg}")

        logger.info(
            "tiktok_stock_updated",
            product_id=product_id,
            sku_id=sku_id,
            quantity=quantity,
        )
        return True

    # ---------------------------------------------------------------- price operations

    async def update_price(
        self,
        product_id: str,
        sku_ids: str | list[str],
        price: float,
    ) -> bool:
        """Update price for one or more SKUs within a product.

        Uses PATCH /product/202309/products/{product_id} with skus[].price.
        TikTok expects price in cents (amount as string of integer cents).
        """
        path = f"/product/202309/products/{product_id}"
        ids = [sku_ids] if isinstance(sku_ids, str) else sku_ids

        body = {
            "skus": [
                {
                    "id": sid,
                    "price": {"amount": str(int(round(price * 100))), "currency": "BRL"},
                }
                for sid in ids
            ]
        }
        resp = await self._post(path, body)

        if resp.get("code") != 0:
            msg = resp.get("message", "Unknown error")
            logger.error(
                "tiktok_update_price_failed",
                product_id=product_id,
                sku_ids=ids,
                code=resp.get("code"),
                message=msg,
            )
            raise RuntimeError(f"TikTok API error: {msg}")

        logger.info(
            "tiktok_price_updated",
            product_id=product_id,
            sku_ids=ids,
            price=price,
        )
        return True

    async def activate_product(self, product_id: str) -> bool:
        """Activate a deactivated product.

        Endpoint: POST /product/202309/products/activate
        """
        path = "/product/202309/products/activate"
        body = {"product_ids": [product_id]}
        resp = await self._post(path, body)

        if resp.get("code") != 0:
            logger.warning(
                "tiktok_activate_failed",
                product_id=product_id,
                code=resp.get("code"),
                message=resp.get("message"),
            )
            return False

        logger.info("tiktok_product_activated", product_id=product_id)
        return True

    async def update_price_with_activation(
        self,
        product_id: str,
        sku_ids: str | list[str],
        price: float,
    ) -> bool:
        """Update price with auto-activation retry.

        If the product is deactivated (seller/platform), activate it first
        and retry the price update.
        """
        try:
            return await self.update_price(product_id, sku_ids, price)
        except RuntimeError as e:
            msg = str(e).lower()
            deactivated_keywords = (
                "current status is not available",
                "seller_deactivated",
                "platform_deactivated",
                "change the product to one of these statuses",
            )
            if any(kw in msg for kw in deactivated_keywords):
                logger.info(
                    "tiktok_price_retry_after_activate",
                    product_id=product_id,
                )
                activated = await self.activate_product(product_id)
                if activated:
                    import asyncio
                    await asyncio.sleep(2)
                    return await self.update_price(product_id, sku_ids, price)
                raise RuntimeError(
                    f"Produto {product_id} desativado no TikTok e não foi possível reativá-lo."
                )
            raise

    # ---------------------------------------------------------------- product search

    async def search_products(
        self, page_size: int = 20, page_token: str | None = None
    ) -> tuple[list[dict], str | None]:
        """Search products. Returns (products, next_page_token)."""
        path = "/product/202309/products/search"
        extra_params: dict[str, str] = {"page_size": str(page_size)}
        if page_token:
            extra_params["page_token"] = page_token

        body: dict[str, Any] = {}
        resp = await self._post(path, body, extra_params)

        if resp.get("code") != 0:
            raise RuntimeError(
                f"TikTok search error: {resp.get('message', 'Unknown')}"
            )

        raw_products = resp.get("data", {}).get("products", [])
        next_token = resp.get("data", {}).get("next_page_token") or None

        products = []
        for p in raw_products:
            skus = []
            for sku in p.get("skus", []):
                inv = sku.get("inventory") or []
                stock = inv[0].get("quantity", 0) if inv else 0
                skus.append(
                    {
                        "id": sku.get("id", ""),
                        "seller_sku": sku.get("seller_sku", ""),
                        "stock": stock,
                    }
                )
            products.append(
                {
                    "product_id": p.get("id", ""),
                    "title": p.get("title", ""),
                    "status": p.get("status", ""),
                    "skus": skus,
                }
            )

        return products, next_token

    # ---------------------------------------------------------------- listings interface

    async def list_listings(
        self,
        *,
        page_size: int = 20,
        max_pages: int | None = None,
    ) -> AsyncIterator[dict]:
        """Async generator yielding normalized listing dicts for the import service."""
        page_token: str | None = None
        page_idx = 0
        while True:
            if max_pages is not None and page_idx >= max_pages:
                return
            products, page_token = await self.search_products(
                page_size=page_size, page_token=page_token
            )
            if not products:
                return
            for p in products:
                for sku in p.get("skus", []):
                    yield {
                        "external_id": p["product_id"],
                        "variation_id": sku["id"],
                        "sku": sku.get("seller_sku") or None,
                        "title": p.get("title", ""),
                        "stock": sku.get("stock"),
                        "status": _tiktok_status_to_listing(p.get("status")),
                        "raw": p,
                    }
            if not page_token:
                return
            page_idx += 1


# ---------------------------------------------------------------- helpers


def _sign(
    app_secret: str,
    path: str,
    query_params: dict[str, str],
    body: str | None = None,
) -> str:
    """Module-level HMAC-SHA256 signer — also used by the OAuth helpers
    that operate without an instantiated TikTokClient."""
    filtered = {
        k: v for k, v in query_params.items() if k not in ("sign", "access_token")
    }
    sign_string = path
    for key in sorted(filtered.keys()):
        sign_string += key + filtered[key]
    if body:
        sign_string += body
    wrapped = app_secret + sign_string + app_secret
    return hmac.new(
        app_secret.encode(), wrapped.encode(), hashlib.sha256
    ).hexdigest()


def _tiktok_status_to_listing(status: str | None) -> str:
    s = (status or "").upper()
    if s in ("LIVE", "ACTIVE"):
        return "active"
    if s in ("SELLER_DEACTIVATED", "PLATFORM_DEACTIVATED"):
        return "paused"
    if s == "FROZEN":
        return "under_review"
    if s == "DELETED":
        return "closed"
    return "inactive"


def _http_error_to_result(e: httpx.HTTPError, qty_before: int | None) -> SyncResult:
    response = getattr(e, "response", None)
    code = response.status_code if response is not None else None
    if code in {429, 502, 503, 504} or code is None:
        return SyncResult(
            status=SyncStatus.RETRYABLE,
            qty_before=qty_before,
            error_code=f"tiktok_http_{code or 'network'}",
            error_detail=str(e)[:500],
        )
    return SyncResult(
        status=SyncStatus.FATAL,
        qty_before=qty_before,
        error_code=f"tiktok_http_{code}",
        error_detail=str(e)[:500],
    )
