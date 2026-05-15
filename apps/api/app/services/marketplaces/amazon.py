"""Amazon SP-API client (Fase 4b.Amazon).

Implements `MarketplaceClient` for Amazon. Uses Listings Items API
(/listings/2021-08-01/items/{sellerId}/{sku}) for stock writes — simpler than
the Feeds API and faster turnaround for low/medium volume.

Auth: LWA-only (current SP-API setup, post-2023 — no AWS Sigv4 required).
Each call carries `x-amz-access-token` and the access token is refreshed via
LWA's /auth/o2/token endpoint.

Credentials shape stored in `integrations.credentials`:
    {
      "lwa_app_id":        str (LWA client_id),
      "lwa_client_secret": str,
      "refresh_token":     str,
      "seller_id":         str,
      "marketplace_id":    str (e.g. A2Q3Y263D00KWC for BR),
      "access_token":      str,
      "expires_at":        int (epoch seconds),
    }
"""

from __future__ import annotations

import asyncio
import gzip
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx
import structlog

from app.services.marketplaces.base import SyncResult, SyncStatus, TestResult

if TYPE_CHECKING:
    from app.models import ProductLink

logger = structlog.get_logger()

LWA_TOKEN_URL = "https://api.amazon.com/auth/o2/token"
SP_API_BASE_NA = "https://sellingpartnerapi-na.amazon.com"
SP_API_BASE_EU = "https://sellingpartnerapi-eu.amazon.com"
SP_API_BASE_FE = "https://sellingpartnerapi-fe.amazon.com"

# Brazil sits in NA region in SP-API.
_REGION_BY_MARKETPLACE: dict[str, str] = {
    "A2Q3Y263D00KWC": SP_API_BASE_NA,  # BR
    "ATVPDKIKX0DER": SP_API_BASE_NA,   # US
    "A2EUQ1WTGCTBG2": SP_API_BASE_NA,  # CA
    "A1AM78C64UM0Y8": SP_API_BASE_NA,  # MX
    "A1F83G8C2ARO7P": SP_API_BASE_EU,  # UK
    "A1PA6795UKMFR9": SP_API_BASE_EU,  # DE
}


class AmazonClient:
    def __init__(self, creds: dict, on_token_refresh=None):
        self.creds = dict(creds)
        self._on_refresh = on_token_refresh

    @property
    def access_token(self) -> str:
        return str(self.creds.get("access_token") or "")

    @property
    def seller_id(self) -> str:
        return str(self.creds.get("seller_id") or "")

    @property
    def marketplace_id(self) -> str:
        return str(self.creds.get("marketplace_id") or "")

    @property
    def expires_at(self) -> int:
        raw = self.creds.get("expires_at")
        if raw in (None, "", 0):
            return 0
        if isinstance(raw, int):
            return raw
        if isinstance(raw, float):
            return int(raw)
        s = str(raw).strip()
        if s.isdigit():
            return int(s)
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return int(dt.timestamp())
        except ValueError:
            return 0

    @property
    def base_url(self) -> str:
        return _REGION_BY_MARKETPLACE.get(self.marketplace_id, SP_API_BASE_NA)

    def _expired(self, skew: int = 30) -> bool:
        return self.expires_at - skew <= int(time.time())

    async def refresh(self) -> None:
        rt = self.creds.get("refresh_token")
        if not rt:
            raise RuntimeError("missing refresh_token")
        async with httpx.AsyncClient(timeout=20.0) as c:
            r = await c.post(
                LWA_TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": rt,
                    "client_id": self.creds.get("lwa_app_id"),
                    "client_secret": self.creds.get("lwa_client_secret"),
                },
            )
            r.raise_for_status()
            payload = r.json() or {}
        self.creds["access_token"] = payload["access_token"]
        if "refresh_token" in payload:
            self.creds["refresh_token"] = payload["refresh_token"]
        expires_in = int(payload.get("expires_in") or 3600)
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
        headers = {
            "x-amz-access-token": self.access_token,
            "Accept": "application/json",
        }
        async with httpx.AsyncClient(timeout=30.0) as c:
            return await c.request(
                method,
                f"{self.base_url}{path}",
                headers=headers,
                params=params,
                json=json,
            )

    async def test_connection(self) -> TestResult:
        try:
            r = await self._request("GET", "/sellers/v1/marketplaceParticipations")
            if r.status_code == 200:
                return TestResult(ok=True, info={"seller_id": self.seller_id})
            return TestResult(ok=False, detail=f"status={r.status_code} body={r.text[:200]}")
        except httpx.HTTPError as e:
            return TestResult(ok=False, detail=f"http_error: {e}")
        except Exception as e:  # noqa: BLE001
            return TestResult(ok=False, detail=f"error: {e}")

    async def list_listings(
        self,
        *,
        max_poll_attempts: int = 30,
        poll_interval: float = 5.0,
    ) -> AsyncIterator[dict]:
        """Yields one normalized listing dict per row of the
        GET_MERCHANT_LISTINGS_ALL_DATA report. Mirrors SSH's getInventory:

          1. POST /reports/2021-06-30/reports         → reportId
          2. Poll /reports/2021-06-30/reports/{id}    → reportDocumentId
          3. GET  /reports/2021-06-30/documents/{id}  → url + compressionAlgorithm
          4. Download (possibly GZIP) and decode (UTF-8/UTF-16/Latin1)
          5. Parse TSV (columns: seller-sku, item-name, asin1, quantity,
             status, price)
        """
        if not self.marketplace_id or not self.seller_id:
            raise RuntimeError("amazon_missing_creds: seller_id or marketplace_id")

        create_r = await self._request(
            "POST",
            "/reports/2021-06-30/reports",
            json={
                "reportType": "GET_MERCHANT_LISTINGS_ALL_DATA",
                "marketplaceIds": [self.marketplace_id],
            },
        )
        if create_r.status_code not in (200, 202):
            raise RuntimeError(
                f"amazon_create_report_failed status={create_r.status_code} body={create_r.text[:300]}"
            )
        report_id = (create_r.json() or {}).get("reportId")
        if not report_id:
            raise RuntimeError("amazon_create_report_no_id")
        logger.info("amazon_report_created", report_id=report_id)

        report_document_id: str | None = None
        last_status = "IN_QUEUE"
        for attempt in range(max_poll_attempts):
            await asyncio.sleep(poll_interval)
            status_r = await self._request(
                "GET", f"/reports/2021-06-30/reports/{report_id}"
            )
            if status_r.status_code != 200:
                continue
            data = status_r.json() or {}
            last_status = data.get("processingStatus") or "UNKNOWN"
            if last_status == "DONE":
                report_document_id = data.get("reportDocumentId")
                break
            if last_status in ("CANCELLED", "FATAL"):
                raise RuntimeError(f"amazon_report_failed status={last_status}")
        if last_status != "DONE" or not report_document_id:
            raise RuntimeError(
                f"amazon_report_timeout last_status={last_status} attempts={max_poll_attempts}"
            )
        logger.info(
            "amazon_report_done", report_id=report_id, document_id=report_document_id
        )

        doc_r = await self._request(
            "GET", f"/reports/2021-06-30/documents/{report_document_id}"
        )
        if doc_r.status_code != 200:
            raise RuntimeError(
                f"amazon_get_document_failed status={doc_r.status_code} body={doc_r.text[:300]}"
            )
        doc = doc_r.json() or {}
        url = doc.get("url")
        compression = doc.get("compressionAlgorithm")
        if not url:
            raise RuntimeError("amazon_document_no_url")

        async with httpx.AsyncClient(timeout=60.0) as c:
            dl = await c.get(url)
        if dl.status_code != 200:
            raise RuntimeError(
                f"amazon_download_failed status={dl.status_code}"
            )
        raw = dl.content
        if compression == "GZIP":
            raw = gzip.decompress(raw)

        text = _decode_amazon_report(raw)
        logger.info("amazon_report_downloaded", bytes=len(raw), chars=len(text))

        for row in _parse_amazon_listings_tsv(text):
            yield row

    async def get_finance_transactions_for_order(self, order_id: str) -> dict:
        r = await self._request(
            "GET",
            "/finances/2024-06-19/transactions",
            params={
                "marketplaceId": self.marketplace_id,
                "relatedIdentifierName": "ORDER_ID",
                "relatedIdentifier": order_id,
            },
        )
        r.raise_for_status()
        return r.json() or {}

    async def update_stock(
        self,
        link: "ProductLink",
        qty: int,
        *,
        bling_store_id: int | None = None,  # ignored for Amazon
    ) -> SyncResult:
        del bling_store_id
        qty_before = link.stock

        # Amazon Listings Items uses the seller's own SKU, not an Amazon ASIN.
        # The Bling/auto-link flow stores SKU in `external_sku` (preferred) or
        # `external_id` as fallback for sellers that key by SKU directly.
        sku = (link.external_sku or link.external_id or "").strip()
        if not sku:
            return SyncResult(
                status=SyncStatus.FATAL,
                qty_before=qty_before,
                error_code="invalid_sku",
                error_detail="link has no external_sku/external_id",
            )
        if not self.seller_id or not self.marketplace_id:
            return SyncResult(
                status=SyncStatus.FATAL,
                qty_before=qty_before,
                error_code="amazon_missing_creds",
                error_detail="seller_id or marketplace_id absent in credentials",
            )

        path = f"/listings/2021-08-01/items/{self.seller_id}/{sku}"
        body = {
            "productType": "PRODUCT",
            "patches": [
                {
                    "op": "replace",
                    "path": "/attributes/fulfillment_availability",
                    "value": [
                        {"fulfillment_channel_code": "DEFAULT", "quantity": qty}
                    ],
                }
            ],
        }
        try:
            r = await self._request(
                "PATCH",
                path,
                params={"marketplaceIds": self.marketplace_id},
                json=body,
            )
        except httpx.HTTPError as e:
            return _http_error_to_result(e, qty_before)

        return _classify_response(r, qty_before, qty_after=qty, sku=sku)

    async def update_price(
        self,
        sku: str,
        price: float,
        *,
        currency: str = "BRL",
    ) -> SyncResult:
        """Push price to an Amazon listing via Listings Items API (PATCH).

        Uses the same PATCH endpoint as update_stock, but patches
        /attributes/purchasable_offer instead of fulfillment_availability.
        """
        sku = sku.strip()
        if not sku:
            return SyncResult(
                status=SyncStatus.FATAL,
                error_code="invalid_sku",
                error_detail="empty sku",
            )
        if not self.seller_id or not self.marketplace_id:
            return SyncResult(
                status=SyncStatus.FATAL,
                error_code="amazon_missing_creds",
                error_detail="seller_id or marketplace_id absent",
            )
        if price <= 0:
            return SyncResult(
                status=SyncStatus.SKIPPED,
                error_code="invalid_price",
                error_detail=f"price={price}",
            )

        path = f"/listings/2021-08-01/items/{self.seller_id}/{sku}"
        body = {
            "productType": "PRODUCT",
            "patches": [
                {
                    "op": "replace",
                    "path": "/attributes/purchasable_offer",
                    "value": [
                        {
                            "marketplace_id": self.marketplace_id,
                            "currency": currency,
                            "our_price": [{"schedule": [{"value_with_tax": price}]}],
                        }
                    ],
                }
            ],
        }
        try:
            r = await self._request(
                "PATCH",
                path,
                params={"marketplaceIds": self.marketplace_id},
                json=body,
            )
        except httpx.HTTPError as e:
            return _http_error_to_result(e, None)

        return _classify_price_response(r, sku, price)

    async def get_listing_price(self, link: "ProductLink") -> float | None:
        """Read the current price from /listings/2021-08-01/items/{seller}/{sku}.
        Amazon stores it under attributes.purchasable_offer[0].our_price[0]
        .schedule[0].value_with_tax — the same path update_price writes to."""
        sku = (link.external_sku or link.external_id or "").strip()
        if not sku or not self.seller_id or not self.marketplace_id:
            return None
        try:
            r = await self._request(
                "GET",
                f"/listings/2021-08-01/items/{self.seller_id}/{sku}",
                params={
                    "marketplaceIds": self.marketplace_id,
                    "includedData": "offers,attributes",
                },
            )
        except Exception:  # noqa: BLE001
            return None
        if r.status_code != 200:
            return None
        body = r.json() or {}
        # Try offers[] first — that's the live offer view; fall back to the
        # attributes patch shape we wrote.
        offers = body.get("offers") or body.get("payload", {}).get("offers") or []
        for offer in offers:
            price = offer.get("price") or {}
            amount = price.get("amount") if isinstance(price, dict) else None
            if amount is not None:
                try:
                    return float(amount)
                except (TypeError, ValueError):
                    return None
        attrs = body.get("attributes") or {}
        po = attrs.get("purchasable_offer") or []
        for entry in po:
            our_price = entry.get("our_price") or []
            for op in our_price:
                schedules = op.get("schedule") or []
                for s in schedules:
                    val = s.get("value_with_tax")
                    if val is not None:
                        try:
                            return float(val)
                        except (TypeError, ValueError):
                            return None
        return None


# ---------------------------------------------------------------- helpers


def _classify_response(
    r: httpx.Response, qty_before: int | None, *, qty_after: int, sku: str
) -> SyncResult:
    """Listings Items returns 200 with `status` + `issues[]` for partial
    failures. Successful patches have status='ACCEPTED' (or 'VALID')."""
    if r.status_code in {429, 502, 503, 504}:
        return SyncResult(
            status=SyncStatus.RETRYABLE,
            qty_before=qty_before,
            error_code=f"http_{r.status_code}",
            error_detail=r.text[:500],
        )
    if r.status_code in {401, 403}:
        return SyncResult(
            status=SyncStatus.FATAL,
            qty_before=qty_before,
            error_code=f"amazon_auth_{r.status_code}",
            error_detail=r.text[:500],
        )
    if r.status_code == 404:
        return SyncResult(
            status=SyncStatus.FATAL,
            qty_before=qty_before,
            error_code="amazon_sku_not_found",
            error_detail=f"sku={sku!r}",
        )

    try:
        payload = r.json() or {}
    except ValueError:
        payload = {}

    if r.status_code >= 400:
        errors = payload.get("errors") or []
        detail = "; ".join(_describe_error(e) for e in errors) or r.text[:500]
        return SyncResult(
            status=SyncStatus.FATAL,
            qty_before=qty_before,
            error_code=f"amazon_http_{r.status_code}",
            error_detail=detail[:500],
        )

    status = (payload.get("status") or "").upper()
    issues = payload.get("issues") or []
    if status == "INVALID" or any(_is_blocking_issue(i) for i in issues):
        detail = "; ".join(_describe_issue(i) for i in issues) or "INVALID"
        return SyncResult(
            status=SyncStatus.FATAL,
            qty_before=qty_before,
            error_code="amazon_invalid_patch",
            error_detail=detail[:500],
            payload={"issues": issues},
        )

    return SyncResult(
        status=SyncStatus.OK,
        qty_before=qty_before,
        qty_after=qty_after,
        payload={"submission_id": payload.get("submissionId"), "status": status or None},
    )


def _http_error_to_result(e: httpx.HTTPError, qty_before: int | None) -> SyncResult:
    response = getattr(e, "response", None)
    code = response.status_code if response is not None else None
    if code in {429, 502, 503, 504} or code is None:
        return SyncResult(
            status=SyncStatus.RETRYABLE,
            qty_before=qty_before,
            error_code=f"amazon_http_{code or 'network'}",
            error_detail=str(e)[:500],
        )
    return SyncResult(
        status=SyncStatus.FATAL,
        qty_before=qty_before,
        error_code=f"amazon_http_{code}",
        error_detail=str(e)[:500],
    )


def _describe_error(err: dict) -> str:
    return f"{err.get('code', '?')}: {err.get('message', '')}"


def _describe_issue(issue: dict) -> str:
    return f"[{issue.get('severity', '?')}] {issue.get('code', '?')}: {issue.get('message', '')}"


def _is_blocking_issue(issue: dict) -> bool:
    return (issue.get("severity") or "").upper() == "ERROR"


def _classify_price_response(
    r: httpx.Response, sku: str, price: float
) -> SyncResult:
    """Classify Amazon price update response (same structure as stock)."""
    if r.status_code in {429, 502, 503, 504}:
        return SyncResult(
            status=SyncStatus.RETRYABLE,
            error_code=f"http_{r.status_code}",
            error_detail=r.text[:500],
        )
    if r.status_code in {401, 403}:
        return SyncResult(
            status=SyncStatus.FATAL,
            error_code=f"amazon_auth_{r.status_code}",
            error_detail=r.text[:500],
        )
    if r.status_code == 404:
        return SyncResult(
            status=SyncStatus.FATAL,
            error_code="amazon_sku_not_found",
            error_detail=f"sku={sku!r}",
        )
    try:
        payload = r.json() or {}
    except ValueError:
        payload = {}
    if r.status_code >= 400:
        errors = payload.get("errors") or []
        detail = "; ".join(_describe_error(e) for e in errors) or r.text[:500]
        return SyncResult(
            status=SyncStatus.FATAL,
            error_code=f"amazon_http_{r.status_code}",
            error_detail=detail[:500],
        )
    status = (payload.get("status") or "").upper()
    issues = payload.get("issues") or []
    if status == "INVALID" or any(_is_blocking_issue(i) for i in issues):
        detail = "; ".join(_describe_issue(i) for i in issues) or "INVALID"
        return SyncResult(
            status=SyncStatus.FATAL,
            error_code="amazon_invalid_patch",
            error_detail=detail[:500],
        )
    return SyncResult(
        status=SyncStatus.OK,
        payload={"sku": sku, "price": price, "submission_id": payload.get("submissionId")},
    )


def _decode_amazon_report(raw: bytes) -> str:
    """Decode an Amazon report buffer with BOM-aware encoding detection.
    Matches SSH's encoding heuristic (UTF-16 LE/BE → UTF-8 BOM → UTF-8 →
    Latin1 fallback for Cp1252 reports)."""
    if len(raw) >= 2 and raw[0] == 0xFF and raw[1] == 0xFE:
        return raw.decode("utf-16-le", errors="replace").lstrip("﻿")
    if len(raw) >= 2 and raw[0] == 0xFE and raw[1] == 0xFF:
        return raw.decode("utf-16-be", errors="replace").lstrip("﻿")
    if len(raw) >= 3 and raw[0] == 0xEF and raw[1] == 0xBB and raw[2] == 0xBF:
        return raw[3:].decode("utf-8", errors="replace")
    try:
        text = raw.decode("utf-8")
        if "�" not in text:
            return text
    except UnicodeDecodeError:
        pass
    return raw.decode("latin-1", errors="replace")


def _parse_amazon_listings_tsv(tsv: str) -> list[dict]:
    """Parse GET_MERCHANT_LISTINGS_ALL_DATA TSV. Returns one normalized dict
    per row (yields a list, kept small enough to fit in memory — Amazon
    reports are bounded by listing count per account).

    Output shape matches the contract consumed by `auto_link._link_amazon`:
        external_id  = seller-sku
        variation_id = asin1 (or None)
        external_sku = seller-sku
        sku          = seller-sku (also used for matching)
        title        = item-name
        stock        = quantity
        listing_type = None
    """
    lines = [ln for ln in tsv.split("\n") if ln.strip()]
    if len(lines) < 2:
        return []
    headers = [h.strip().lower() for h in lines[0].split("\t")]
    def _idx(*names: str) -> int:
        for n in names:
            if n in headers:
                return headers.index(n)
        return -1
    sku_i = _idx("seller-sku")
    title_i = _idx("item-name")
    asin_i = _idx("asin1", "asin")
    qty_i = _idx("quantity")
    status_i = _idx("status")
    price_i = _idx("price")

    out: list[dict] = []
    for ln in lines[1:]:
        cols = ln.split("\t")
        sku = cols[sku_i].strip() if 0 <= sku_i < len(cols) else ""
        if not sku:
            continue
        asin = cols[asin_i].strip() if 0 <= asin_i < len(cols) else ""
        qty_raw = cols[qty_i] if 0 <= qty_i < len(cols) else "0"
        try:
            qty = int(qty_raw or "0")
        except ValueError:
            qty = 0
        price_raw = cols[price_i] if 0 <= price_i < len(cols) else ""
        price_cents: int | None = None
        if price_raw:
            try:
                price_cents = int(round(float(price_raw) * 100))
            except ValueError:
                price_cents = None
        amz_status = (cols[status_i].strip() if 0 <= status_i < len(cols) else "Active") or "Active"
        out.append({
            "external_id": sku,
            "variation_id": asin or None,
            "external_sku": sku,
            "sku": sku,
            "title": cols[title_i].strip() if 0 <= title_i < len(cols) else "",
            "asin": asin or None,
            "stock": qty,
            "price": price_cents,
            "status": "active" if amz_status.lower() == "active" else "inactive",
            "listing_type": None,
        })
    return out
