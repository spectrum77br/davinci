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

import time
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
        return int(self.creds.get("expires_at") or 0)

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
