"""Temu Open API client (full implementation).

Implements `MarketplaceClient` for Temu using their Open API.
Supports: test_connection, update_stock, update_price, search_products,
list_listings.

Auth: MD5 signature per Temu Open API spec.
Credentials shape stored in `integrations.credentials`:
    {
      "app_key":      str,
      "app_secret":   str,
      "access_token": str,
      "region":       "us" | "eu" | "global",   (default: global)
    }
"""
from __future__ import annotations

import hashlib
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

TEMU_REGION_URLS: dict[str, str] = {
    "us": "https://openapi-b-us.temu.com/openapi/router",
    "eu": "https://openapi-b-eu.temu.com/openapi/router",
    "global": "https://openapi-b-global.temu.com/openapi/router",
}


class TemuClient:
    def __init__(self, creds: dict, on_token_refresh=None):
        self.creds = dict(creds)
        self._on_refresh = on_token_refresh
        region = str(creds.get("region") or "global").strip()
        self._base_url = TEMU_REGION_URLS.get(region, TEMU_REGION_URLS["global"])

    @property
    def app_key(self) -> str:
        return str(self.creds.get("app_key") or "").strip()

    @property
    def app_secret(self) -> str:
        return str(self.creds.get("app_secret") or "").strip()

    @property
    def access_token(self) -> str:
        return str(self.creds.get("access_token") or "").strip()

    # ---------------------------------------------------------------- signing

    def _generate_sign(self, params: dict[str, Any]) -> str:
        """Generate MD5 signature per Temu Open API spec.

        1. Sort all params by key (ASCII ascending)
        2. Concatenate as key1value1key2value2...
        3. Prepend and append app_secret
        4. MD5 hash, uppercase
        """
        sorted_keys = sorted(params.keys())
        parts: list[str] = []
        for key in sorted_keys:
            val = params[key]
            if isinstance(val, (dict, list)):
                val = json.dumps(val, separators=(",", ":"), ensure_ascii=False)
            parts.append(f"{key}{val}")

        concatenated = "".join(parts)
        sign_string = f"{self.app_secret}{concatenated}{self.app_secret}"
        return hashlib.md5(sign_string.encode("utf-8")).hexdigest().upper()

    def _timestamp(self) -> int:
        return int(time.time())

    # ---------------------------------------------------------------- HTTP helpers

    async def _request(
        self, api_type: str, request_params: dict[str, Any] | None = None
    ) -> dict:
        """Make a POST request to the Temu API."""
        common_params: dict[str, Any] = {
            "app_key": self.app_key,
            "access_token": self.access_token,
            "timestamp": self._timestamp(),
            "data_type": "JSON",
            "type": api_type,
            "version": "V1",
        }
        all_params = {**common_params, **(request_params or {})}
        sign = self._generate_sign(all_params)
        payload = {**all_params, "sign": sign}

        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.post(
                f"{self._base_url}?app_secret={self.app_secret}",
                json=payload,
                headers={"Content-Type": "application/json"},
            )

        if r.status_code != 200:
            return {"success": False, "error_msg": f"HTTP {r.status_code}: {r.text[:300]}"}
        return r.json()

    def _is_success(self, result: dict) -> bool:
        """Check if Temu API response indicates success."""
        return (
            result.get("success") is True
            or result.get("error_code") == 0
            or result.get("errorCode") == 0
        )

    def _error_message(self, result: dict) -> str:
        return str(
            result.get("error_msg")
            or result.get("errorMessage")
            or result.get("message")
            or "Erro desconhecido"
        )

    # ---------------------------------------------------------------- MarketplaceClient interface

    async def test_connection(self) -> TestResult:
        required = ("app_key", "app_secret", "access_token")
        missing = [k for k in required if not self.creds.get(k)]
        if missing:
            return TestResult(
                ok=False, detail=f"missing_credentials: {','.join(missing)}"
            )
        try:
            result = await self._request("bg.local.goods.cats.get", {"parentCatId": 0})
            if self._is_success(result):
                return TestResult(ok=True, detail="Conexão com Temu estabelecida com sucesso!")
            return TestResult(ok=False, detail=f"Temu error: {self._error_message(result)}")
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
        """Push stock to a Temu product SKU.

        link.external_id = Temu product_sku_id
        link.variation_id = warehouse_id (optional)
        """
        del bling_store_id
        qty_before = link.stock
        product_sku_id = (link.external_id or "").strip()

        if not product_sku_id:
            return SyncResult(
                status=SyncStatus.FATAL,
                qty_before=qty_before,
                error_code="temu_missing_sku_id",
                error_detail=f"external_id={link.external_id!r}",
            )

        try:
            warehouse_id = (link.variation_id or "").strip() or None
            await self._update_stock(product_sku_id, qty, warehouse_id=warehouse_id)
            return SyncResult(
                status=SyncStatus.OK,
                qty_before=qty_before,
                qty_after=qty,
                payload={"product_sku_id": product_sku_id},
            )
        except httpx.HTTPError as e:
            return _http_error_to_result(e, qty_before)
        except Exception as e:  # noqa: BLE001
            return SyncResult(
                status=SyncStatus.FATAL,
                qty_before=qty_before,
                error_code="temu_exception",
                error_detail=str(e)[:500],
            )

    # ---------------------------------------------------------------- stock operations

    async def _update_stock(
        self, product_sku_id: str, quantity: int, warehouse_id: str | None = None
    ) -> dict:
        """Update stock for a single SKU.

        API: bg.inventory.quantity.update
        Body: stockList = [{ productSkuId, warehouseId?, quantity }]
        """
        stock_item: dict[str, Any] = {
            "productSkuId": int(product_sku_id),
            "quantity": quantity,
        }
        if warehouse_id:
            stock_item["warehouseId"] = int(warehouse_id)

        result = await self._request(
            "bg.inventory.quantity.update",
            {"stockList": json.dumps([stock_item])},
        )

        if not self._is_success(result):
            msg = self._error_message(result)
            logger.error(
                "temu_update_stock_failed",
                product_sku_id=product_sku_id,
                message=msg,
            )
            raise RuntimeError(f"Temu API error: {msg}")

        logger.info(
            "temu_stock_updated",
            product_sku_id=product_sku_id,
            quantity=quantity,
        )
        return result

    # ---------------------------------------------------------------- price operations

    async def update_price(
        self,
        product_sku_id: str,
        price: float,
        *,
        currency: str = "BRL",
    ) -> bool:
        """Update price for a single SKU.

        API: bg.goods.price.update
        Body: priceList = [{ productSkuId, price, currency }]
        """
        price_item: dict[str, Any] = {
            "productSkuId": int(product_sku_id),
            "price": int(round(price * 100)),  # Temu expects cents
            "currency": currency,
        }

        result = await self._request(
            "bg.goods.price.update",
            {"priceList": json.dumps([price_item])},
        )

        if not self._is_success(result):
            msg = self._error_message(result)
            logger.error(
                "temu_update_price_failed",
                product_sku_id=product_sku_id,
                message=msg,
            )
            raise RuntimeError(f"Temu API error: {msg}")

        logger.info(
            "temu_price_updated",
            product_sku_id=product_sku_id,
            price=price,
        )
        return True

    # ---------------------------------------------------------------- product search

    async def search_products(
        self, page: int = 1, page_size: int = 50
    ) -> tuple[list[dict], int]:
        """Search products. Returns (products, total_count)."""
        result = await self._request(
            "bg.goods.list.get",
            {"page": page, "pageSize": page_size},
        )

        if not self._is_success(result):
            raise RuntimeError(
                f"Temu search error: {self._error_message(result)}"
            )

        data = result.get("result") or result.get("data") or {}
        items = data.get("goodsList") or data.get("list") or []
        total = data.get("total") or data.get("totalCount") or len(items)

        products = []
        for item in items:
            skus = []
            for sku in item.get("productSkuList") or item.get("skuList") or []:
                skus.append(
                    {
                        "product_sku_id": str(sku.get("productSkuId") or ""),
                        "product_skc_id": str(sku.get("productSkcId") or ""),
                        "sku": sku.get("extCode") or sku.get("skuCode") or "",
                        "stock": sku.get("quantity") or sku.get("stock") or 0,
                        "warehouse_id": str(sku.get("warehouseId") or ""),
                    }
                )
            products.append(
                {
                    "product_id": str(item.get("productId") or item.get("goodsId") or ""),
                    "title": item.get("productName") or item.get("goodsName") or "",
                    "status": item.get("status") or "",
                    "skus": skus,
                }
            )

        return products, int(total)

    # ---------------------------------------------------------------- listings interface

    async def list_listings(
        self,
        *,
        page_size: int = 50,
        max_pages: int | None = None,
    ) -> AsyncIterator[dict]:
        """Async generator yielding normalized listing dicts for the import service."""
        page = 1
        while True:
            if max_pages is not None and page > max_pages:
                return
            products, total = await self.search_products(page=page, page_size=page_size)
            if not products:
                return
            for p in products:
                for sku in p.get("skus", []):
                    yield {
                        "external_id": sku.get("product_sku_id") or p["product_id"],
                        "variation_id": sku.get("warehouse_id") or None,
                        "sku": sku.get("sku") or None,
                        "title": p.get("title", ""),
                        "stock": sku.get("stock"),
                        "status": _temu_status_to_listing(p.get("status")),
                        "raw": p,
                    }
            if page * page_size >= total:
                return
            page += 1


# ---------------------------------------------------------------- helpers


def _temu_status_to_listing(status: Any) -> str:
    s = str(status or "").lower()
    if s in ("1", "on_sale", "active", "normal"):
        return "active"
    if s in ("0", "off_sale", "inactive"):
        return "paused"
    if s in ("2", "under_review"):
        return "under_review"
    return "inactive"


def _http_error_to_result(e: httpx.HTTPError, qty_before: int | None) -> SyncResult:
    response = getattr(e, "response", None)
    code = response.status_code if response is not None else None
    if code in {429, 502, 503, 504} or code is None:
        return SyncResult(
            status=SyncStatus.RETRYABLE,
            qty_before=qty_before,
            error_code=f"temu_http_{code or 'network'}",
            error_detail=str(e)[:500],
        )
    return SyncResult(
        status=SyncStatus.FATAL,
        qty_before=qty_before,
        error_code=f"temu_http_{code}",
        error_detail=str(e)[:500],
    )
