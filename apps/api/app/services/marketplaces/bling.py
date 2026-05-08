"""Bling API v3 client.

OAuth2 (authorization code), automatic refresh on 401.
Credentials stored in `integrations.credentials` shape:
    {
      "client_id": str,
      "client_secret": str,
      "access_token": str,
      "refresh_token": str,
      "token_type": "Bearer",
      "scope": str,
      "expires_at": int (epoch seconds),
    }
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import httpx
import structlog

from app.config import get_settings
from app.services.marketplaces.base import SyncResult, SyncStatus, TestResult

logger = structlog.get_logger()

BLING_AUTH_URL = "https://www.bling.com.br/Api/v3/oauth/authorize"
BLING_TOKEN_URL = "https://www.bling.com.br/Api/v3/oauth/token"
BLING_API_BASE = "https://www.bling.com.br/Api/v3"

# Default page size for `/produtos`. Bling caps at 100.
BLING_PRODUCTS_PAGE_SIZE = 100


class BlingCloudflareError(RuntimeError):
    """Raised when Bling returns the Cloudflare HTML challenge instead of JSON."""


def _looks_like_cf_html(resp: httpx.Response) -> bool:
    ctype = resp.headers.get("content-type", "")
    if "html" not in ctype.lower():
        return False
    head = resp.text[:512].lower()
    return "<html" in head or "cloudflare" in head or "just a moment" in head


class BlingClient:
    def __init__(
        self,
        creds: dict,
        on_token_refresh=None,
    ):
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
            "client_id": s.bling_client_id,
            "state": state,
            "redirect_uri": s.bling_redirect_uri,
        }
        return f"{BLING_AUTH_URL}?{urlencode(params)}"

    @staticmethod
    async def exchange_code(code: str) -> dict:
        s = get_settings()
        async with httpx.AsyncClient(timeout=20.0) as c:
            r = await c.post(
                BLING_TOKEN_URL,
                auth=(s.bling_client_id, s.bling_client_secret),
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": s.bling_redirect_uri,
                },
            )
            r.raise_for_status()
            return _normalize_token(r.json())

    def _client_creds(self) -> tuple[str, str]:
        """Per-integration client_id/secret when present, else env fallback."""
        s = get_settings()
        cid = str(self.creds.get("client_id") or s.bling_client_id or "")
        csec = str(self.creds.get("client_secret") or s.bling_client_secret or "")
        return cid, csec

    async def refresh(self) -> None:
        rt = self.creds.get("refresh_token")
        if not rt:
            raise RuntimeError("missing refresh_token")
        cid, csec = self._client_creds()
        async with httpx.AsyncClient(timeout=20.0) as c:
            r = await c.post(
                BLING_TOKEN_URL,
                auth=(cid, csec),
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={"grant_type": "refresh_token", "refresh_token": rt},
            )
            r.raise_for_status()
            self.creds.update(_normalize_token(r.json(), prev=self.creds))
        if self._on_refresh:
            await self._on_refresh(self.creds)

    async def _request(
        self, method: str, path: str, *, params: dict | None = None, json: Any = None
    ) -> httpx.Response:
        if self._expired():
            await self.refresh()
        url = f"{BLING_API_BASE}{path}"
        headers = {"Authorization": f"Bearer {self.access_token}", "Accept": "application/json"}

        # Bling occasionally serves a Cloudflare challenge (HTTP 403 + HTML) under
        # heavy load. Retry with exponential backoff up to 3 attempts (B12).
        last_exc: Exception | None = None
        delay = 1.0
        for attempt in range(3):
            async with httpx.AsyncClient(timeout=30.0) as c:
                r = await c.request(method, url, headers=headers, params=params, json=json)
            if r.status_code == 401 and attempt == 0:
                await self.refresh()
                headers["Authorization"] = f"Bearer {self.access_token}"
                continue
            cf = _looks_like_cf_html(r)
            if r.status_code in (429, 503) or (r.status_code == 403 and cf):
                last_exc = BlingCloudflareError(
                    f"status={r.status_code} cf_html={cf}"
                )
                logger.warning(
                    "bling_retry",
                    attempt=attempt + 1,
                    status=r.status_code,
                    path=path,
                )
                await asyncio.sleep(delay)
                delay *= 2
                continue
            return r
        if last_exc:
            raise last_exc
        return r

    async def list_lojas(self) -> list[dict]:
        r = await self._request("GET", "/lojas")
        r.raise_for_status()
        body = r.json()
        return body.get("data", [])

    async def list_products_page(
        self,
        *,
        pagina: int = 1,
        limite: int = BLING_PRODUCTS_PAGE_SIZE,
    ) -> list[dict]:
        """Single page of `/produtos`. Bling pagination via `pagina` + `limite` query params."""
        r = await self._request(
            "GET",
            "/produtos",
            params={"pagina": pagina, "limite": limite},
        )
        r.raise_for_status()
        return r.json().get("data", []) or []

    async def list_products(self, *, start_page: int = 1) -> AsyncIterator[dict]:
        """Iterate every product, paging until Bling returns an empty page."""
        page = start_page
        while True:
            items = await self.list_products_page(pagina=page)
            if not items:
                return
            for it in items:
                yield it
            if len(items) < BLING_PRODUCTS_PAGE_SIZE:
                return
            page += 1

    async def get_product(self, bling_product_id: int) -> dict:
        r = await self._request("GET", f"/produtos/{bling_product_id}")
        r.raise_for_status()
        return r.json().get("data") or {}

    async def update_stock_by_id(
        self,
        bling_product_id: int,
        qty: int,
        *,
        bling_store_id: int | None = None,
        operation: str = "B",
    ) -> dict:
        """Raw Bling stock write. POST /Api/v3/estoques.

        When `bling_store_id` is provided it is sent as `idLoja` so the change
        reflects on the correct channel inside Bling. Body shape per Bling docs:
            { "produto": {"id": <id>}, "operacao": "B"|"S"|"E", "quantidade": <qty>,
              "deposito": {"id": <id>}, "idLoja": <int?> }
        """
        body: dict[str, Any] = {
            "produto": {"id": bling_product_id},
            "operacao": operation,
            "quantidade": qty,
        }
        if bling_store_id is not None:
            body["idLoja"] = bling_store_id
        r = await self._request("POST", "/estoques", json=body)
        r.raise_for_status()
        return r.json().get("data") or {}

    async def update_stock(
        self,
        link: Any,
        qty: int,
        *,
        bling_store_id: int | None = None,
    ) -> SyncResult:
        """ABC-conformant wrapper. `link` is a `ProductLink` whose `external_id`
        carries the Bling produto.id. See `services.marketplaces.base.MarketplaceClient`.
        """
        try:
            bling_product_id = int(link.external_id)
        except (TypeError, ValueError):
            return SyncResult(
                status=SyncStatus.FATAL,
                error_code="invalid_external_id",
                error_detail=f"link.external_id={link.external_id!r}",
            )
        qty_before = link.stock
        try:
            data = await self.update_stock_by_id(
                bling_product_id, qty, bling_store_id=bling_store_id
            )
        except BlingCloudflareError as e:
            return SyncResult(
                status=SyncStatus.RETRYABLE,
                qty_before=qty_before,
                error_code="bling_cloudflare",
                error_detail=str(e),
            )
        except httpx.HTTPStatusError as e:
            code = e.response.status_code if e.response is not None else None
            return SyncResult(
                status=SyncStatus.RETRYABLE if code in (429, 502, 503, 504) else SyncStatus.FATAL,
                qty_before=qty_before,
                error_code=f"http_{code}",
                error_detail=e.response.text[:500] if e.response is not None else str(e),
            )
        return SyncResult(
            status=SyncStatus.OK,
            qty_before=qty_before,
            qty_after=qty,
            payload=data if isinstance(data, dict) else {},
        )

    async def update_price(
        self,
        bling_product_id: int,
        price: float | int | str,
        *,
        bling_store_id: int | None = None,
    ) -> dict:
        """Stub for Fase 4. Bling endpoint: PATCH /Api/v3/produtos/{id}/precos
        with optional `idLoja` query param to scope to a channel.
        """
        params: dict[str, Any] = {}
        if bling_store_id is not None:
            params["idLoja"] = bling_store_id
        r = await self._request(
            "PATCH",
            f"/produtos/{bling_product_id}/precos",
            params=params or None,
            json={"preco": float(price)},
        )
        r.raise_for_status()
        return r.json().get("data") or {}

    async def test_connection(self) -> TestResult:
        try:
            r = await self._request("GET", "/produtos", params={"pagina": 1, "limite": 1})
            if r.status_code == 200:
                data = r.json().get("data") or []
                return TestResult(ok=True, info={"sample_count": len(data)})
            return TestResult(ok=False, detail=f"status={r.status_code} body={r.text[:200]}")
        except httpx.HTTPError as e:
            return TestResult(ok=False, detail=f"http_error: {e}")
        except Exception as e:  # noqa: BLE001
            return TestResult(ok=False, detail=f"error: {e}")


def parse_bling_product(raw: dict) -> dict:
    """Normalize Bling `/produtos` item to the shape used by `BlingPreviewItem`.

    Bling fields (v3): id, nome, codigo (sku), preco, estoque{saldoVirtualTotal},
    preco_custo (varies by endpoint), imagemURL.
    """
    estoque = raw.get("estoque") or {}
    if isinstance(estoque, dict):
        stock = estoque.get("saldoVirtualTotal")
        if stock is None:
            stock = estoque.get("disponivel") or estoque.get("saldoFisicoTotal")
    else:
        stock = None
    sku = (raw.get("codigo") or "").strip() or None
    cost = raw.get("precoCusto") or raw.get("preco_custo")
    image = raw.get("imagemURL") or raw.get("midia", {}).get("imagem", {}).get("url")
    return {
        "bling_product_id": int(raw["id"]),
        "sku": sku,
        "name": raw.get("nome") or raw.get("descricao") or "",
        "cost_price": cost,
        "price": raw.get("preco"),
        "stock": int(stock) if stock is not None else None,
        "image_url": image,
    }


def _normalize_token(payload: dict, prev: dict | None = None) -> dict:
    """Convert Bling token response to internal credential dict."""
    expires_in = int(payload.get("expires_in") or 21600)
    out: dict = dict(prev or {})
    out["access_token"] = payload["access_token"]
    if "refresh_token" in payload:
        out["refresh_token"] = payload["refresh_token"]
    out["token_type"] = payload.get("token_type", "Bearer")
    out["scope"] = payload.get("scope", out.get("scope", ""))
    out["expires_at"] = int(time.time()) + expires_in
    out["_obtained_at"] = datetime.now(UTC).isoformat()
    return out
