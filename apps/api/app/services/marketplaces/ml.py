"""Mercado Livre API client (Fase 4b.ML).

Implements `MarketplaceClient` for ML; covers PRD §11 Fase 4b.ML and resolves
bugs B1 (auto-link zeroing stock), B2 (links with stock=0/last_sync_at=NULL),
B3 (variation_not_found intermittent).

OAuth2 (authorization code), automatic refresh on 401.

Credential shape stored in `integrations.credentials`:
    {
      "client_id":     str,
      "client_secret": str,
      "access_token":  str,
      "refresh_token": str,
      "user_id":       int (ML seller id, populated after first /users/me),
      "expires_at":    int (epoch seconds),
    }

ML stock writes:
- Listings without variations  : PUT /items/{item_id}        body={"available_quantity": qty}
- Listings *with* variations   : PUT /items/{item_id}        body={"variations": [{"id": var_id, "available_quantity": qty}]}

If the variation_id stored locally is no longer present on ML (variations were
edited in the seller dashboard), `update_stock` walks the variations on the
listing matching by `seller_custom_field` (== local SKU). When found, the link
is repointed (B3); when not, the link is marked `requires_review`.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

import httpx
import structlog

from app.config import get_settings
from app.services.marketplaces.base import SyncResult, SyncStatus, TestResult

if TYPE_CHECKING:
    from app.models import ProductLink

logger = structlog.get_logger()

ML_AUTH_URL = "https://auth.mercadolivre.com.br/authorization"
ML_TOKEN_URL = "https://api.mercadolibre.com/oauth/token"
ML_API_BASE = "https://api.mercadolibre.com"


class MercadoLivreClient:
    def __init__(self, creds: dict, on_token_refresh=None):
        self.creds = dict(creds)
        self._on_refresh = on_token_refresh

    @property
    def access_token(self) -> str | None:
        return self.creds.get("access_token")

    @property
    def expires_at(self) -> int:
        return int(self.creds.get("expires_at") or 0)

    def _expired(self, skew: int = 30) -> bool:
        # expires_at == 0 means unknown — trust access_token until ML returns 401.
        if self.expires_at == 0:
            return False
        return self.expires_at - skew <= int(time.time())

    @staticmethod
    def authorize_url(state: str) -> str:
        s = get_settings()
        params = {
            "response_type": "code",
            "client_id": s.ml_client_id,
            "redirect_uri": s.ml_redirect_uri,
            "state": state,
        }
        return f"{ML_AUTH_URL}?{urlencode(params)}"

    @staticmethod
    async def exchange_code(code: str) -> dict:
        s = get_settings()
        async with httpx.AsyncClient(timeout=20.0) as c:
            r = await c.post(
                ML_TOKEN_URL,
                headers={"Accept": "application/json"},
                data={
                    "grant_type": "authorization_code",
                    "client_id": s.ml_client_id,
                    "client_secret": s.ml_client_secret,
                    "code": code,
                    "redirect_uri": s.ml_redirect_uri,
                },
            )
            r.raise_for_status()
            creds = _normalize_token(r.json())
            creds["client_id"] = s.ml_client_id
            creds["client_secret"] = s.ml_client_secret
            return creds

    def _client_creds(self) -> tuple[str, str]:
        """Per-integration client_id/secret when present, else env fallback."""
        s = get_settings()
        cid = str(self.creds.get("client_id") or s.ml_client_id or "")
        csec = str(self.creds.get("client_secret") or s.ml_client_secret or "")
        return cid, csec

    async def refresh(self) -> None:
        rt = self.creds.get("refresh_token")
        if not rt:
            raise RuntimeError("missing refresh_token")
        cid, csec = self._client_creds()
        if not cid or not csec:
            raise RuntimeError("missing client_id or client_secret")
        async with httpx.AsyncClient(timeout=20.0) as c:
            r = await c.post(
                ML_TOKEN_URL,
                headers={"Accept": "application/json"},
                data={
                    "grant_type": "refresh_token",
                    "client_id": cid,
                    "client_secret": csec,
                    "refresh_token": rt,
                },
            )
            if r.status_code >= 400:
                raise RuntimeError(
                    f"ml_refresh_failed status={r.status_code} body={r.text[:300]}"
                )
            self.creds.update(_normalize_token(r.json(), prev=self.creds))
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
        url = f"{ML_API_BASE}{path}"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
        }
        delay = 1.0
        for attempt in range(3):
            async with httpx.AsyncClient(timeout=30.0) as c:
                r = await c.request(method, url, headers=headers, params=params, json=json)
            if r.status_code == 401 and attempt == 0:
                await self.refresh()
                headers["Authorization"] = f"Bearer {self.access_token}"
                continue
            if r.status_code in (429, 502, 503, 504):
                logger.warning(
                    "ml_retry", attempt=attempt + 1, status=r.status_code, path=path
                )
                await asyncio.sleep(delay)
                delay *= 2
                continue
            return r
        return r

    async def test_connection(self) -> TestResult:
        try:
            r = await self._request("GET", "/users/me")
            if r.status_code == 200:
                data = r.json() or {}
                if data.get("id") and self.creds.get("user_id") != data["id"]:
                    self.creds["user_id"] = data["id"]
                    if self._on_refresh:
                        await self._on_refresh(self.creds)
                return TestResult(ok=True, info={"id": data.get("id"), "nick": data.get("nickname")})
            return TestResult(ok=False, detail=f"status={r.status_code} body={r.text[:200]}")
        except httpx.HTTPError as e:
            return TestResult(ok=False, detail=f"http_error: {e}")
        except Exception as e:  # noqa: BLE001
            return TestResult(ok=False, detail=f"error: {e}")

    async def get_item(self, item_id: str) -> dict:
        r = await self._request("GET", f"/items/{item_id}")
        r.raise_for_status()
        return r.json() or {}

    async def update_stock(
        self,
        link: ProductLink,
        qty: int,
        *,
        bling_store_id: int | None = None,  # ignored on ML side
    ) -> SyncResult:
        """ABC entrypoint. Resolves variation, applies B1 guard, dispatches to
        the correct ML endpoint, and classifies the outcome.

        B1: never write `available_quantity=0` when caller has positive stock.
        B3: re-resolve variation_id by `seller_sku` when the stored id is gone.
        """
        del bling_store_id  # not used; signature kept for ABC parity
        qty_before = link.stock

        # B1 guard ------------------------------------------------------------
        if qty == 0 and (qty_before or 0) > 0:
            return SyncResult(
                status=SyncStatus.SKIPPED,
                qty_before=qty_before,
                error_code="b1_guard_zero_block",
                error_detail=(
                    "refused to push qty=0 when source stock was positive; "
                    "verify origin before unblocking"
                ),
            )

        item_id = link.external_id
        try:
            item = await self.get_item(item_id)
        except httpx.HTTPStatusError as e:
            code = e.response.status_code if e.response is not None else None
            if code == 404:
                return SyncResult(
                    status=SyncStatus.FATAL,
                    qty_before=qty_before,
                    error_code="ml_item_not_found",
                    error_detail=f"item_id={item_id}",
                )
            return _map_http_error(e, qty_before, "ml_get_item_failed")

        listing_status = (item.get("status") or "").lower()
        if listing_status in {"closed", "paused"}:
            return SyncResult(
                status=SyncStatus.SKIPPED,
                qty_before=qty_before,
                error_code=f"ml_listing_{listing_status}",
            )

        variations = item.get("variations") or []
        seller_sku = (link.external_sku or "").strip() or None

        if variations:
            target_var, repointed = _resolve_variation(
                variations,
                stored_var_id=link.variation_id,
                seller_sku=seller_sku,
            )
            if target_var is None:
                return SyncResult(
                    status=SyncStatus.REQUIRES_REVIEW,
                    qty_before=qty_before,
                    error_code="ml_variation_not_found",
                    error_detail=(
                        f"variation_id={link.variation_id!r} gone and no "
                        f"variation matches seller_sku={seller_sku!r}"
                    ),
                    payload={"item_id": item_id, "variations_seen": len(variations)},
                )

            new_var_id = str(target_var["id"])
            payload_extra: dict[str, Any] = {}
            if repointed:
                link.variation_id = new_var_id
                payload_extra["variation_repointed_from"] = link.variation_id

            try:
                r = await self._request(
                    "PUT",
                    f"/items/{item_id}",
                    json={
                        "variations": [
                            {"id": int(new_var_id), "available_quantity": qty}
                        ]
                    },
                )
            except httpx.HTTPError as e:
                return _map_http_error(e, qty_before, "ml_put_variation_failed")
            if r.status_code >= 400:
                return _map_status_error(r, qty_before, "ml_put_variation_status")
            return SyncResult(
                status=SyncStatus.OK,
                qty_before=qty_before,
                qty_after=qty,
                payload={"item_id": item_id, "variation_id": new_var_id, **payload_extra},
            )

        # No variations on the listing -- single-item update.
        try:
            r = await self._request(
                "PUT",
                f"/items/{item_id}",
                json={"available_quantity": qty},
            )
        except httpx.HTTPError as e:
            return _map_http_error(e, qty_before, "ml_put_item_failed")
        if r.status_code >= 400:
            return _map_status_error(r, qty_before, "ml_put_item_status")
        return SyncResult(
            status=SyncStatus.OK,
            qty_before=qty_before,
            qty_after=qty,
            payload={"item_id": item_id},
        )

    async def update_price(
        self,
        item_id: str,
        price: float,
        *,
        variation_id: str | None = None,
    ) -> SyncResult:
        """Push price to a single ML listing (or one of its variations).

        SSH semantics: ML refuses price changes on paused listings with
        `item.price.not_modifiable`. When that happens we activate the
        listing, wait ~2s for ML to settle, retry the price update, then
        re-pause to leave the state intact.
        """
        if price <= 0:
            return SyncResult(
                status=SyncStatus.SKIPPED,
                error_code="invalid_price",
                error_detail=f"price={price}",
            )
        body: dict[str, Any]
        if variation_id:
            body = {"variations": [{"id": int(variation_id), "price": price}]}
        else:
            body = {"price": price}

        async def _put_price() -> tuple[int, dict]:
            try:
                r = await self._request("PUT", f"/items/{item_id}", json=body)
            except httpx.HTTPError as e:
                return -1, {"_http_error": str(e)}
            try:
                payload = r.json() if r.content else {}
            except Exception:  # noqa: BLE001
                payload = {}
            return r.status_code, payload

        status, payload = await _put_price()
        if status == -1:
            return _map_http_error(
                httpx.HTTPError(payload.get("_http_error", "unknown")),
                None,
                "ml_put_price_failed",
            )
        if status < 400:
            return SyncResult(
                status=SyncStatus.OK,
                qty_after=None,
                payload={
                    "item_id": item_id,
                    "variation_id": variation_id,
                    "price": price,
                },
            )

        # SSH retry path: detect not_modifiable + pause → activate → wait → push
        # → repause.
        message = (payload.get("message") or "").lower() if isinstance(payload, dict) else ""
        cause_list = payload.get("cause") or [] if isinstance(payload, dict) else []
        cause_codes = " ".join(str(c.get("code", "")) for c in cause_list).lower()
        if "not_modifiable" in message or "not_modifiable" in cause_codes:
            try:
                # 1. activate (status="active")
                ar = await self._request(
                    "PUT", f"/items/{item_id}", json={"status": "active"}
                )
                if ar.status_code >= 400:
                    return _map_status_error(
                        ar, None, "ml_unpause_for_price_failed"
                    )
                await asyncio.sleep(2)
                # 2. retry price push
                status2, payload2 = await _put_price()
                # 3. re-pause regardless of step 2 outcome
                try:
                    await self._request(
                        "PUT", f"/items/{item_id}", json={"status": "paused"}
                    )
                except Exception:  # noqa: BLE001
                    pass
                if status2 == -1:
                    return _map_http_error(
                        httpx.HTTPError(payload2.get("_http_error", "unknown")),
                        None,
                        "ml_put_price_failed_after_unpause",
                    )
                if status2 < 400:
                    return SyncResult(
                        status=SyncStatus.OK,
                        qty_after=None,
                        payload={
                            "item_id": item_id,
                            "variation_id": variation_id,
                            "price": price,
                            "via": "unpause_repause",
                        },
                    )
                # Reconstruct an httpx.Response-ish object via mapping helpers
                # is awkward — fall through to the generic path below.
                status = status2
                payload = payload2
            except Exception as e:  # noqa: BLE001
                return SyncResult(
                    status=SyncStatus.FATAL,
                    error_code="ml_unpause_retry_failed",
                    error_detail=str(e)[:500],
                )

        # Generic error mapping for the non-retryable path.
        class _R:
            status_code = status
            content = json.dumps(payload).encode() if payload else b""

            def json(self):
                return payload

        return _map_status_error(_R(), None, "ml_put_price_status")

    async def list_listings(
        self,
        *,
        page_size: int = 50,
        max_pages: int | None = None,
    ) -> AsyncIterator[dict]:
        """Yields one normalized listing dict per ML item.

        Walks /users/{seller}/items/search to enumerate ids, then
        /items?ids=...&attributes=... in batches of 20 (ML's multi-get cap)
        to fetch listing details.
        """
        seller_id = self.creds.get("user_id")
        if not seller_id:
            r = await self._request("GET", "/users/me")
            r.raise_for_status()
            seller_id = (r.json() or {}).get("id")
            if seller_id and self.creds.get("user_id") != seller_id:
                self.creds["user_id"] = seller_id
                if self._on_refresh:
                    await self._on_refresh(self.creds)
        if not seller_id:
            return

        offset = 0
        page_idx = 0
        while True:
            if max_pages is not None and page_idx >= max_pages:
                break
            r = await self._request(
                "GET",
                f"/users/{seller_id}/items/search",
                params={"limit": page_size, "offset": offset},
            )
            if r.status_code != 200:
                raise RuntimeError(
                    f"ml_search_failed status={r.status_code} body={r.text[:200]}"
                )
            data = r.json() or {}
            ids = data.get("results") or []
            if not ids:
                break
            for chunk_start in range(0, len(ids), 20):
                chunk = ids[chunk_start : chunk_start + 20]
                rr = await self._request(
                    "GET",
                    "/items",
                    params={"ids": ",".join(chunk)},
                )
                if rr.status_code != 200:
                    logger.warning(
                        "ml_multiget_failed", status=rr.status_code, body=rr.text[:200]
                    )
                    continue
                for entry in rr.json() or []:
                    if entry.get("code") != 200:
                        continue
                    body = entry.get("body") or {}
                    yield _normalize_ml_item(body)
            paging = data.get("paging") or {}
            total = int(paging.get("total") or 0)
            offset += page_size
            page_idx += 1
            if offset >= total:
                break


# ---------------------------------------------------------------- helpers

_ML_LISTING_TYPE_MAP = {
    "gold_pro": "ml premium",
    "gold_premium": "ml premium",
    "gold_special": "ml classico",
}


def _map_ml_listing_type(listing_type_id: str | None) -> str | None:
    if not listing_type_id:
        return None
    return _ML_LISTING_TYPE_MAP.get(listing_type_id.lower())


def _normalize_ml_item(body: dict) -> dict:
    sku = (body.get("seller_custom_field") or "").strip()
    if not sku:
        for attr in body.get("attributes") or []:
            if (attr.get("id") or "").upper() == "SELLER_SKU":
                sku = (attr.get("value_name") or "").strip()
                break
    raw_price = body.get("price")
    price_cents: int | None = None
    if raw_price is not None:
        try:
            price_cents = int(round(float(raw_price) * 100))
        except (TypeError, ValueError):
            price_cents = None
    status = (body.get("status") or "").lower() or "active"
    return {
        "external_id": str(body.get("id") or ""),
        "sku": sku or None,
        "title": body.get("title") or "",
        "description": None,
        "price": price_cents,
        "stock": body.get("available_quantity"),
        "status": status if status in {
            "active", "paused", "closed", "under_review", "inactive"
        } else "inactive",
        "category": body.get("category_id"),
        "thumbnail_url": body.get("thumbnail") or body.get("secure_thumbnail"),
        "listing_type": _map_ml_listing_type(body.get("listing_type_id")),
        "raw": body,
    }


def _resolve_variation(
    variations: list[dict],
    *,
    stored_var_id: str | None,
    seller_sku: str | None,
) -> tuple[dict | None, bool]:
    """Return (variation_dict, repointed?). `repointed=True` means the stored
    id no longer matches but we found a fresh one via `seller_sku`."""
    if stored_var_id:
        for v in variations:
            if str(v.get("id")) == str(stored_var_id):
                return v, False
    if not seller_sku:
        return None, False
    sku_norm = seller_sku.strip().lower()
    for v in variations:
        for attr in v.get("attributes") or []:
            if (attr.get("id") or "").upper() == "SELLER_SKU":
                val = (attr.get("value_name") or "").strip().lower()
                if val and val == sku_norm:
                    return v, True
        scf = (v.get("seller_custom_field") or "").strip().lower()
        if scf and scf == sku_norm:
            return v, True
    return None, False


def _map_http_error(e: httpx.HTTPError, qty_before: int | None, code: str) -> SyncResult:
    status: SyncStatus = SyncStatus.RETRYABLE
    detail = str(e)[:500]
    response = getattr(e, "response", None)
    http_code = response.status_code if response is not None else None
    if http_code in {400, 401, 403, 404, 422}:
        status = SyncStatus.FATAL
    return SyncResult(
        status=status,
        qty_before=qty_before,
        error_code=f"{code}_{http_code}" if http_code else code,
        error_detail=detail,
    )


def _map_status_error(r: httpx.Response, qty_before: int | None, code: str) -> SyncResult:
    if r.status_code in {429, 502, 503, 504}:
        status = SyncStatus.RETRYABLE
    elif r.status_code in {401, 403}:
        status = SyncStatus.FATAL
    elif r.status_code in {400, 422}:
        status = SyncStatus.FATAL
    else:
        status = SyncStatus.RETRYABLE
    return SyncResult(
        status=status,
        qty_before=qty_before,
        error_code=f"{code}_{r.status_code}",
        error_detail=r.text[:500],
    )


def _normalize_token(payload: dict, prev: dict | None = None) -> dict:
    expires_in = int(payload.get("expires_in") or 21600)
    out: dict = dict(prev or {})
    out["access_token"] = payload["access_token"]
    if "refresh_token" in payload:
        out["refresh_token"] = payload["refresh_token"]
    if "user_id" in payload:
        out["user_id"] = payload["user_id"]
    out["scope"] = payload.get("scope", out.get("scope", ""))
    out["expires_at"] = int(time.time()) + expires_in
    out["_obtained_at"] = datetime.now(UTC).isoformat()
    return out
