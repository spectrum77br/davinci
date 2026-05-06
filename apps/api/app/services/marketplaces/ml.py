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
import time
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
            return _normalize_token(r.json())

    async def refresh(self) -> None:
        rt = self.creds.get("refresh_token")
        if not rt:
            raise RuntimeError("missing refresh_token")
        s = get_settings()
        async with httpx.AsyncClient(timeout=20.0) as c:
            r = await c.post(
                ML_TOKEN_URL,
                headers={"Accept": "application/json"},
                data={
                    "grant_type": "refresh_token",
                    "client_id": s.ml_client_id,
                    "client_secret": s.ml_client_secret,
                    "refresh_token": rt,
                },
            )
            r.raise_for_status()
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
        link: "ProductLink",
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


# ---------------------------------------------------------------- helpers

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
