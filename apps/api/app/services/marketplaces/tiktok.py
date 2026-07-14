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
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

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

# TikTok returns `access_token_expire_in` / `refresh_token_expire_in` as an
# ABSOLUTE epoch timestamp (the instant of expiry), not a relative duration —
# e.g. an access token issued now comes back as `now + 604800` (7 days). The
# old code did `now + expire_in`, doubling it to ~year 2083, which made every
# refresh path (lazy, cron, column) believe the token was valid for decades
# and never refresh it — so tokens silently died at their real 7-day expiry
# and all pushes failed with 105002 "Expired credentials". Normalise both
# conventions defensively: a value already in the future is treated as an
# absolute expiry; a small value is treated as a duration from `now`.
_EXPIRED_CREDS_CODES = {105002, 36009005}


def _norm_expiry(value: Any, now: int) -> int:
    v = int(value or 0)
    if v <= 0:
        return 0
    return v if v > now else now + v

# Explicit connect+read timeout for every product/search/inventory call. The
# bare `timeout=15.0` used before is a single number httpx spreads across all
# phases, but its read timeout is *per socket read* (it resets on each byte),
# so a TikTok endpoint dribbling bytes could keep a request alive far past 15s
# and wedge the auto-link job — which was the "TikTok trava e não vincula"
# symptom. A tighter, explicit connect + a bounded read is the floor; the
# auto-link loop stacks an asyncio.wait_for wall on top as belt-and-suspenders.
TIKTOK_DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=10.0)


class TikTokClient:
    def __init__(
        self,
        creds: dict,
        on_token_refresh=None,
        *,
        timeout: httpx.Timeout | float | None = None,
    ):
        self.creds = dict(creds)
        self._on_refresh = on_token_refresh
        self._timeout = timeout if timeout is not None else TIKTOK_DEFAULT_TIMEOUT
        # Per-instance memo for promotion lookups so a product with several
        # variations doesn't re-search + re-fetch the shop's activities on
        # every variation during one price push.
        self._promo_search_cache: list[dict] | None = None
        self._promo_activity_cache: dict[str, dict | None] = {}

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
        data["token_expires_at"] = _norm_expiry(
            data.get("access_token_expire_in"), now
        )
        data["refresh_token_expires_at"] = _norm_expiry(
            data.get("refresh_token_expire_in"), now
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
        data["token_expires_at"] = _norm_expiry(
            data.get("access_token_expire_in"), now
        )
        data["refresh_token_expires_at"] = _norm_expiry(
            data.get("refresh_token_expire_in"), now
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

    async def refresh(self) -> None:
        """Force a token refresh via the refresh_token and persist the new
        tokens through the `on_token_refresh` callback. Used by the periodic
        token-refresh cron and as the self-heal on an expired-credentials
        response. No-op when the refresh_token / app creds are missing."""
        rt = self.refresh_token_value
        if not rt or not self.app_key or not self.app_secret:
            return
        data = await TikTokClient.refresh_access_token(
            rt, self.app_key, self.app_secret
        )
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

    async def _ensure_fresh_token(self, *, force: bool = False) -> None:
        """Refresh access_token before requests if it's near expiry (or when
        `force` is set, e.g. after an expired-credentials response).

        Skipped when refresh_token is missing (e.g. legacy creds). When not
        forcing, also skipped if the stored expiry is still comfortably ahead.
        """
        rt = self.refresh_token_value
        if not rt or not self.app_key or not self.app_secret:
            return
        if not force:
            exp = self.token_expires_at
            if not exp:
                return
            if exp - int(time.time()) > TOKEN_REFRESH_BUFFER_SEC:
                return
        try:
            await self.refresh()
        except Exception as e:  # noqa: BLE001
            logger.warning("tiktok_refresh_failed", err=str(e))

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

    @staticmethod
    def _expired_creds(resp: dict) -> bool:
        """True when a 200-OK TikTok body signals an expired access token
        (code 105002 / 36009005) — the trigger to force-refresh and retry."""
        return resp.get("code") in _EXPIRED_CREDS_CODES

    async def _get(
        self,
        path: str,
        extra_params: dict[str, str] | None = None,
        *,
        _retried: bool = False,
    ) -> dict:
        await self._ensure_fresh_token()
        params = self._build_get_params(path, extra_params)
        async with httpx.AsyncClient(timeout=self._timeout) as c:
            r = await c.get(
                f"{TIKTOK_BASE_URL}{path}",
                params=params,
                headers={
                    "x-tts-access-token": self.access_token,
                    "content-type": "application/json",
                },
            )
        if r.status_code == 200:
            body = r.json()
            if self._expired_creds(body) and not _retried:
                await self._ensure_fresh_token(force=True)
                return await self._get(path, extra_params, _retried=True)
            return body
        return {"code": r.status_code, "message": r.text[:500]}

    async def _post(
        self,
        path: str,
        body: dict | None = None,
        extra_params: dict[str, str] | None = None,
        *,
        _retried: bool = False,
    ) -> dict:
        await self._ensure_fresh_token()
        body_str = json.dumps(body or {}, separators=(",", ":"), ensure_ascii=False)
        params = self._build_post_params(path, body_str, extra_params)
        async with httpx.AsyncClient(timeout=self._timeout) as c:
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
            resp = r.json()
            if self._expired_creds(resp) and not _retried:
                await self._ensure_fresh_token(force=True)
                return await self._post(path, body, extra_params, _retried=True)
            return resp
        return {"code": r.status_code, "message": r.text[:500]}

    async def _put(
        self,
        path: str,
        body: dict | None = None,
        extra_params: dict[str, str] | None = None,
        *,
        _retried: bool = False,
    ) -> dict:
        await self._ensure_fresh_token()
        body_str = json.dumps(body or {}, separators=(",", ":"), ensure_ascii=False)
        params = self._build_post_params(path, body_str, extra_params)
        async with httpx.AsyncClient(timeout=self._timeout) as c:
            r = await c.put(
                f"{TIKTOK_BASE_URL}{path}",
                content=body_str,
                params=params,
                headers={
                    "x-tts-access-token": self.access_token,
                    "content-type": "application/json",
                },
            )
        if r.status_code == 200:
            resp = r.json()
            if self._expired_creds(resp) and not _retried:
                await self._ensure_fresh_token(force=True)
                return await self._put(path, body, extra_params, _retried=True)
            return resp
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
                names = ", ".join(
                    (s.get("name") or s.get("shop_name") or "?") for s in shops
                )
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

    async def get_order_settlements(self, order_id: str) -> dict:
        """Settlement / statement transactions for one order.

        TikTok v202309 Finance API:
            GET /finance/202309/orders/{order_id}/statement_transactions

        The old V1 path (/api/finance/order/settlements) was retired and now
        answers HTTP 410. This endpoint also requires the app to hold the
        Finance access scope; without it TikTok returns 401 code 105005.
        `_get` adds shop_cipher + signing + access_token automatically.
        """
        return await self._get(
            f"/finance/202309/orders/{order_id}/statement_transactions"
        )

    async def update_stock(
        self,
        link: "ProductLink",
        qty: int,
        *,
        bling_store_id: int | None = None,
        force: bool = False,
    ) -> SyncResult:
        """Push stock to a TikTok product SKU.

        `force` accepted for ABC parity (manual sync bypass). TikTok has no
        zero-stock guard, so the flag is a no-op here.

        link.external_id = TikTok product_id
        link.variation_id = TikTok sku_id

        On a "current status is not available" error (product deactivated on
        TikTok side), the product is reactivated and the stock push retried
        once after a 2-second delay.
        """
        del bling_store_id, force
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
            await self._update_product_stock_with_activation(product_id, sku_id, qty)
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

    async def _update_product_stock_with_activation(
        self, product_id: str, sku_id: str, quantity: int
    ) -> bool:
        """Wraps `_update_product_stock` with auto-activation retry, mirroring
        `update_price_with_activation`. TikTok rejects stock writes on
        deactivated SKUs with a "current status is not available" message;
        reactivating the product and waiting 2s usually unblocks the write.
        """
        try:
            return await self._update_product_stock(product_id, sku_id, quantity)
        except RuntimeError as e:
            msg = str(e).lower()
            deactivated_keywords = (
                "current status is not available",
                "seller_deactivated",
                "platform_deactivated",
                "change the product to one of these statuses",
            )
            if not any(kw in msg for kw in deactivated_keywords):
                raise
            logger.info(
                "tiktok_stock_retry_after_activate",
                product_id=product_id,
                sku_id=sku_id,
            )
            activated = await self.activate_product(product_id)
            if not activated:
                raise RuntimeError(
                    f"Produto {product_id} desativado no TikTok e não foi possível reativá-lo."
                ) from e
            import asyncio

            await asyncio.sleep(2)
            return await self._update_product_stock(product_id, sku_id, quantity)

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
        """Update price for one or more SKUs within a product — SSH parity.

        Uses POST /product/202309/products/{product_id}/prices/update — the
        dedicated price endpoint. TikTok expects:
          - amount and sale_price as strings with 2 decimal places ("299.00"),
            not integer cents,
          - currency "BRL",
          - both amount AND sale_price must be present.

        Empty / falsy sku_ids are dropped before the call; if nothing remains
        we surface that loudly so the caller can mark the link as needing
        attention instead of pushing into nowhere.
        """
        ids_in: list[str] = [sku_ids] if isinstance(sku_ids, str) else list(sku_ids)
        ids = [sid for sid in ids_in if sid]
        if not ids:
            raise RuntimeError(
                f"SKU ID não encontrado no vínculo para produto {product_id}"
            )
        path = f"/product/202309/products/{product_id}/prices/update"
        price_str = f"{price:.2f}"
        body = {
            "skus": [
                {
                    "id": sid,
                    "price": {
                        "amount": price_str,
                        "currency": "BRL",
                        "sale_price": price_str,
                    },
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

    async def get_listing_price(self, link) -> float | None:
        """Read the current price from GET /product/202309/products/{id}.
        Each SKU under skus[] has its own price object; we look up the one
        matching link.variation_id, or fall back to the first one when the
        listing has no variations stored on the link."""
        path = f"/product/202309/products/{link.external_id}"
        try:
            resp = await self._get(path)
        except Exception:  # noqa: BLE001
            return None
        if not isinstance(resp, dict) or resp.get("code") != 0:
            return None
        data = resp.get("data") or {}
        skus = data.get("skus") or []
        if not skus:
            return None
        target = (link.variation_id or "").strip()
        chosen = None
        if target:
            for sku in skus:
                if str(sku.get("id") or "") == target:
                    chosen = sku
                    break
        chosen = chosen or skus[0]
        price = chosen.get("price") or {}
        # TikTok responds with amount as a string in cents OR in reais
        # depending on version — try both.
        amount = price.get("sale_price") or price.get("tax_exclusive_price") or price.get("amount")
        if amount is None:
            return None
        try:
            v = float(amount)
        except (TypeError, ValueError):
            return None
        # Heuristic: if the value is "large and integer-shaped" assume cents.
        # Real prices in BRL are typically < 100000.
        if v > 100000 and v == int(v):
            return v / 100.0
        return v

    async def get_listing_snapshot(self, link: "ProductLink") -> dict | None:
        """Current seller_sku + title for the SKU this link points to
        (product_id=external_id, sku_id=variation_id). Returns
        ``{"sku", "title"}`` or None when the listing/SKU can't be read.

        Used by the on-demand reconcile (link_reconcile) to detect that the
        operator changed the seller_sku ON the TikTok listing — TikTok keeps
        the internal sku_id stable across a seller_sku edit, so we match by
        sku_id and read the (possibly new) seller_sku off it. No fall back to
        the first SKU: if the stored sku_id is gone we can't safely reconcile."""
        product_id = (link.external_id or "").strip()
        sku_id = (link.variation_id or "").strip()
        if not product_id:
            return None
        try:
            resp = await self._get(f"/product/202309/products/{product_id}")
        except Exception:  # noqa: BLE001
            return None
        if not isinstance(resp, dict) or resp.get("code") != 0:
            return None
        data = resp.get("data") or {}
        title = data.get("title")
        skus = data.get("skus") or []
        chosen = None
        if sku_id:
            for s in skus:
                if str(s.get("id") or "") == sku_id:
                    chosen = s
                    break
        elif len(skus) == 1:
            chosen = skus[0]
        if chosen is None:
            return None
        return {"sku": (chosen.get("seller_sku") or "").strip() or None, "title": title}

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

    # ------------------------------------------------------- discount (promotion) price

    async def update_discount_price(
        self,
        product_id: str,
        sku_id: str,
        price: float,
    ) -> SyncResult:
        """Push price to a TikTok listing via its ACTIVE PROMOTION, mirroring
        Shopee.update_price.

        Like Shopee (which never touches the base price and edits the active
        "Desconto de Loja" instead), TikTok's displayed deal price comes from
        an ONGOING Fixed-Price promotion activity. We locate the ongoing
        FIXED_PRICE activity that already carries this product/SKU and update
        its `activity_price_amount`. If the product isn't enrolled yet but a
        single ongoing FIXED_PRICE activity exists, we add it (parity with
        Shopee's add_discount_item fallback). No active fixed-price promotion
        -> FATAL so the user knows to create one. A FLASHSALE promotion is
        surfaced as SKIPPED (its price can't be edited mid-run), matching the
        Shopee flash-sale behaviour. The base price is never written.
        """
        if price <= 0:
            return SyncResult(
                status=SyncStatus.SKIPPED,
                error_code="invalid_price",
                error_detail=f"price={price}",
            )
        product_id = (product_id or "").strip()
        sku_id = (sku_id or "").strip()
        if not product_id:
            return SyncResult(
                status=SyncStatus.FATAL,
                error_code="tiktok_missing_ids",
                error_detail=f"product_id={product_id!r}",
            )

        try:
            activity, entry, kind = await self._find_product_activity(
                product_id, sku_id
            )
        except Exception as e:  # noqa: BLE001
            return SyncResult(
                status=SyncStatus.FATAL,
                error_code="tiktok_activity_lookup_failed",
                error_detail=str(e)[:500],
            )

        if kind == "flash":
            return SyncResult(
                status=SyncStatus.SKIPPED,
                error_code="tiktok_flashsale_active",
                error_detail=(
                    f"Produto {product_id} está em Promoção Relâmpago (Flash "
                    "Deal); o preço só pode ser alterado após o fim da flash sale."
                ),
            )
        if kind == "ambiguous":
            return SyncResult(
                status=SyncStatus.FATAL,
                error_code="tiktok_multiple_activities",
                error_detail=(
                    f"Produto {product_id} não está em nenhuma Promoção de "
                    "Preço Fixo e há mais de uma ativa — adicione o produto à "
                    "promoção certa no painel do TikTok antes de empurrar preço."
                ),
            )
        if kind == "none" or activity is None:
            return SyncResult(
                status=SyncStatus.FATAL,
                error_code="tiktok_no_active_activity",
                error_detail=(
                    f"Produto {product_id} sem Promoção de Preço Fixo ativa. "
                    "Crie uma Promoção (Fixed Price / Preço Fixo) no painel do "
                    "TikTok antes de tentar empurrar preço."
                ),
            )

        try:
            ok = await self._update_activity_product_price(
                activity, entry, product_id, sku_id, price
            )
        except Exception as e:  # noqa: BLE001
            return SyncResult(
                status=SyncStatus.FATAL,
                error_code="tiktok_discount_push_failed",
                error_detail=str(e)[:500],
            )
        if not ok:
            return SyncResult(
                status=SyncStatus.FATAL,
                error_code="tiktok_discount_push_failed",
                error_detail="update-activity-product returned non-zero",
            )
        return SyncResult(
            status=SyncStatus.OK,
            payload={
                "product_id": product_id,
                "sku_id": sku_id,
                "price": price,
                "via": "update_activity_product"
                if kind == "update"
                else "add_activity_product",
                "activity_id": activity.get("activity_id") or activity.get("id"),
            },
        )

    async def _search_ongoing_activities(self) -> list[dict]:
        """Return the shop's ONGOING promotion activities (metadata only).

        POST /promotion/202309/activities/search with status=ONGOING, paged.
        Cached on the instance for the lifetime of this client so a multi-
        variation price push doesn't re-search per variation.
        """
        if self._promo_search_cache is not None:
            return self._promo_search_cache
        activities: list[dict] = []
        page_token = ""
        for _ in range(10):  # hard page cap
            body: dict[str, Any] = {"status": "ONGOING", "page_size": 100}
            if page_token:
                body["page_token"] = page_token
            resp = await self._post(
                "/promotion/202309/activities/search", body
            )
            if resp.get("code") != 0:
                raise RuntimeError(
                    f"TikTok search-activities error: {resp.get('message', 'Unknown')}"
                )
            data = resp.get("data") or {}
            activities.extend(data.get("activities") or [])
            page_token = data.get("next_page_token") or ""
            if not page_token:
                break
        self._promo_search_cache = activities
        return activities

    async def _get_activity(self, activity_id: str) -> dict | None:
        """GET /promotion/202309/activities/{id} -> the activity `data`
        (with products[]), or None. Cached per instance."""
        if activity_id in self._promo_activity_cache:
            return self._promo_activity_cache[activity_id]
        resp = await self._get(f"/promotion/202309/activities/{activity_id}")
        full = resp.get("data") if isinstance(resp, dict) and resp.get("code") == 0 else None
        self._promo_activity_cache[activity_id] = full
        return full

    @staticmethod
    def _match_product_entry(
        full: dict, product_id: str, sku_id: str
    ) -> dict | None:
        """Return the product entry from a get-activity payload that carries
        `product_id` (and, for VARIATION-level activities, `sku_id`), else
        None."""
        level = (full.get("product_level") or "").upper()
        for p in full.get("products") or []:
            if str(p.get("id") or "") != str(product_id):
                continue
            if level == "VARIATION":
                if not sku_id:
                    continue
                for s in p.get("skus") or []:
                    if str(s.get("id") or "") == str(sku_id):
                        return p
                continue
            return p
        return None

    async def _find_product_activity(
        self, product_id: str, sku_id: str
    ) -> tuple[dict | None, dict | None, str]:
        """Locate the ongoing promotion that governs `product_id`/`sku_id`.

        Returns (activity_full, product_entry, kind) where kind is:
          - "update": product already in an ongoing FIXED_PRICE activity;
          - "add":    product not enrolled, but exactly one ongoing FIXED_PRICE
                      activity exists -> safe to add it there;
          - "flash":  product is in an ongoing FLASHSALE activity (skip);
          - "ambiguous": product not enrolled and >1 ongoing FIXED_PRICE;
          - "none":   no ongoing FIXED_PRICE activity at all.
        """
        activities = await self._search_ongoing_activities()
        fixed = [a for a in activities if (a.get("activity_type") or "") == "FIXED_PRICE"]
        flash = [a for a in activities if (a.get("activity_type") or "") == "FLASHSALE"]

        # 1) already enrolled in a FIXED_PRICE activity -> update
        for a in fixed:
            full = await self._get_activity(str(a.get("id") or ""))
            if not full:
                continue
            entry = self._match_product_entry(full, product_id, sku_id)
            if entry is not None:
                return full, entry, "update"

        # 2) enrolled in a FLASHSALE activity -> skip (can't edit)
        for a in flash:
            full = await self._get_activity(str(a.get("id") or ""))
            if not full:
                continue
            if self._match_product_entry(full, product_id, sku_id) is not None:
                return full, None, "flash"

        # 3) not enrolled anywhere: add only if a single FIXED_PRICE exists
        if len(fixed) == 1:
            full = await self._get_activity(str(fixed[0].get("id") or ""))
            if full:
                return full, None, "add"
            return None, None, "none"
        if len(fixed) > 1:
            return None, None, "ambiguous"
        return None, None, "none"

    async def _update_activity_product_price(
        self,
        activity: dict,
        entry: dict | None,
        product_id: str,
        sku_id: str,
        price: float,
    ) -> bool:
        """PUT /promotion/202309/activities/{id}/products — set the deal price
        (`activity_price_amount`) for the product/SKU. Handles both adding and
        editing (TikTok's update endpoint does both). Raises on API error."""
        activity_id = str(activity.get("activity_id") or activity.get("id") or "")
        level = (activity.get("product_level") or "").upper()
        price_str = f"{price:.2f}"

        product_obj: dict[str, Any] = {"id": str(product_id)}
        if level == "VARIATION":
            # Spec: at VARIATION level product-level and sku-level quantity
            # fields must be -1; the deal price lives on the SKU.
            product_obj["quantity_limit"] = -1
            product_obj["quantity_per_user"] = -1
            product_obj["skus"] = [
                {
                    "id": str(sku_id),
                    "activity_price_amount": price_str,
                    "quantity_limit": -1,
                    "quantity_per_user": -1,
                }
            ]
        else:
            # PRODUCT level: deal price on the product, skus must be [].
            # Preserve existing quantity limits (they can't be decreased);
            # default to -1 (unlimited) when adding a fresh product.
            ql = (entry or {}).get("quantity_limit")
            qpu = (entry or {}).get("quantity_per_user")
            product_obj["activity_price_amount"] = price_str
            product_obj["quantity_limit"] = ql if isinstance(ql, int) and ql != 0 else -1
            product_obj["quantity_per_user"] = (
                qpu if isinstance(qpu, int) and qpu != 0 else -1
            )
            product_obj["skus"] = []

        body = {"products": [product_obj], "activity_id": activity_id}
        resp = await self._put(
            f"/promotion/202309/activities/{activity_id}/products", body
        )
        if resp.get("code") != 0:
            msg = resp.get("message", "Unknown error")
            logger.error(
                "tiktok_update_activity_product_failed",
                product_id=product_id,
                sku_id=sku_id,
                activity_id=activity_id,
                code=resp.get("code"),
                message=msg,
            )
            raise RuntimeError(f"TikTok API error: {msg}")
        logger.info(
            "tiktok_discount_price_updated",
            product_id=product_id,
            sku_id=sku_id,
            activity_id=activity_id,
            price=price,
        )
        return True

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
