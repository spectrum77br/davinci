"""Amazon Advertising API client (Sponsored Products v3) for the Marketing module.

Why a separate file from `services/marketplaces/amazon.py`: SP-API and
Advertising API are two distinct services from Amazon's side — different
client_id/secret/refresh_token, different OAuth endpoints, different
base URLs, and different authentication headers
(`Amazon-Advertising-API-ClientId` + `Amazon-Advertising-API-Scope`).

Credentials live in settings (env): `amazon_ads_client_id`,
`amazon_ads_client_secret`, `amazon_ads_refresh_token`,
`amazon_ads_profile_id`. When any are blank we return
`AmazonAdsMissingCreds` early so the sync orchestrator can skip
gracefully instead of erroring.

Reports are asynchronous: we POST /reporting/reports to start one, then
poll /reporting/reports/{id} every ~5s until status == COMPLETED, then
GET the gzipped JSON from the returned URL. Two safety nets:
  - hard timeout of 90s per report (most return in 15-30s)
  - the orchestrator catches any AmazonAdsError and falls back to the
    last persisted MarketingMetric row

Dates: Amazon uses YYYYMMDD (no dashes). Helper `_fmt_date` handles it.
"""
from __future__ import annotations

import asyncio
import gzip
import json
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx
import structlog

from app.config import get_settings

logger = structlog.get_logger()

# Cluster endpoints. SA shares the NA cluster — that's the right choice
# for Brazilian sellers per Amazon's docs.
_AMAZON_ADS_BASE = {
    "na": "https://advertising-api.amazon.com",
    "eu": "https://advertising-api-eu.amazon.com",
    "fe": "https://advertising-api-fe.amazon.com",
}
_AMAZON_TOKEN_URL = "https://api.amazon.com/auth/o2/token"

# Report polling: every _POLL_INTERVAL seconds, up to _POLL_TIMEOUT total.
# Amazon typically returns in 15-30s; we cap at 90s before falling back.
_POLL_INTERVAL = 5.0
_POLL_TIMEOUT = 90.0

# Soft rate limit for ad endpoints. Amazon's documented ceiling is much
# higher (~10 req/s per scope) but we only need a handful per sync.
_RATE_LIMIT_SECONDS = 0.5


class AmazonAdsError(RuntimeError):
    """Generic Amazon Ads HTTP failure."""

    def __init__(self, status: int, code: str, message: str, path: str):
        super().__init__(f"{status} {code}: {message} (path={path})")
        self.status = status
        self.code = code
        self.message = message
        self.path = path


class AmazonAdsMissingCreds(AmazonAdsError):
    """Raised when env vars are blank — orchestrator treats as skip, not error."""

    def __init__(self):
        super().__init__(0, "missing_credentials", "AMAZON_ADS_* env vars not set", "")


@dataclass(slots=True)
class AmazonCampaign:
    campaign_id: str
    name: str
    state: str  # "enabled" | "paused" | "archived"
    daily_budget: float | None
    spend: float
    impressions: int
    clicks: int
    sales_14d: float
    acos: float | None


def _fmt_date(d: date) -> str:
    return d.strftime("%Y%m%d")


def _amazon_creds_present() -> bool:
    s = get_settings()
    return all(
        [
            s.amazon_ads_client_id,
            s.amazon_ads_client_secret,
            s.amazon_ads_refresh_token,
            s.amazon_ads_profile_id,
        ]
    )


class AmazonAdsClient:
    """Stateless client — refreshes its own access_token on construction
    (or first call). No DB state; safe to instantiate per-sync. The
    refresh token lives in env, not per-integration, because Amazon
    Advertising operates at the seller-account level (one profile per
    marketplace), not per-shop."""

    def __init__(self) -> None:
        if not _amazon_creds_present():
            raise AmazonAdsMissingCreds()
        s = get_settings()
        self._client_id = s.amazon_ads_client_id
        self._client_secret = s.amazon_ads_client_secret
        self._refresh_token = s.amazon_ads_refresh_token
        self._profile_id = s.amazon_ads_profile_id
        self._base = _AMAZON_ADS_BASE.get(s.amazon_ads_region or "na", _AMAZON_ADS_BASE["na"])
        self._access_token: str | None = None

    async def _ensure_token(self) -> None:
        if self._access_token is not None:
            return
        async with httpx.AsyncClient(timeout=20.0) as c:
            r = await c.post(
                _AMAZON_TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": self._refresh_token,
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
            )
            if r.status_code >= 400:
                raise AmazonAdsError(
                    r.status_code, "auth_failed",
                    f"refresh_token rejected: {r.text[:200]}",
                    _AMAZON_TOKEN_URL,
                )
            payload = r.json() or {}
        self._access_token = str(payload.get("access_token") or "")
        if not self._access_token:
            raise AmazonAdsError(500, "no_access_token", "Amazon returned empty token", _AMAZON_TOKEN_URL)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Amazon-Advertising-API-ClientId": self._client_id,
            "Amazon-Advertising-API-Scope": str(self._profile_id),
            "Content-Type": "application/vnd.spCampaign.v3+json",
        }

    async def _request(
        self, method: str, path: str, *, json_body: Any = None, params: dict | None = None,
        accept: str | None = None,
    ) -> dict:
        await self._ensure_token()
        headers = self._headers()
        if accept:
            headers["Accept"] = accept
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.request(
                method, f"{self._base}{path}",
                headers=headers, params=params, json=json_body,
            )
        if r.status_code == 401:
            # Refresh and retry once.
            self._access_token = None
            await self._ensure_token()
            headers = self._headers()
            if accept:
                headers["Accept"] = accept
            async with httpx.AsyncClient(timeout=30.0) as c:
                r = await c.request(
                    method, f"{self._base}{path}",
                    headers=headers, params=params, json=json_body,
                )
        await asyncio.sleep(_RATE_LIMIT_SECONDS)
        if r.status_code >= 400:
            raise AmazonAdsError(
                r.status_code,
                "http_error",
                r.text[:300],
                path,
            )
        try:
            return r.json() or {}
        except Exception:  # noqa: BLE001
            return {"_raw": r.text}

    # ─── campaigns ────────────────────────────────────────────────────────

    async def list_campaigns(self) -> list[dict]:
        """All ENABLED + PAUSED Sponsored Products campaigns. Excludes
        ARCHIVED — they don't accrue spend and inflate the list."""
        data = await self._request(
            "POST", "/sp/campaigns/list",
            json_body={
                "stateFilter": {"include": ["ENABLED", "PAUSED"]},
                "maxResults": 100,
            },
            accept="application/vnd.spCampaign.v3+json",
        )
        return data.get("campaigns") or []

    async def get_budget_usage(self, campaign_ids: list[str]) -> dict:
        if not campaign_ids:
            return {"usageDetails": []}
        return await self._request(
            "POST", "/sp/campaigns/budget/usage",
            json_body={"campaignIds": campaign_ids},
        )

    async def get_remaining_daily_budget(self, campaigns: list[dict]) -> float:
        """Sum of enabled-campaign daily budgets minus today's spend.
        Mirrors the ML "credit" concept."""
        total_budget = sum(
            _safe_float((c.get("budget") or {}).get("budget"))
            for c in campaigns
            if c.get("state") == "ENABLED"
        )
        ids = [str(c.get("campaignId")) for c in campaigns if c.get("state") == "ENABLED"]
        if not ids:
            return max(total_budget, 0.0)
        try:
            usage = await self.get_budget_usage(ids)
            spend_today = sum(
                _safe_float(u.get("spend"))
                for u in usage.get("usageDetails") or []
            )
        except AmazonAdsError as e:
            logger.warning("amazon_ads_budget_usage_failed", err=str(e)[:200])
            spend_today = 0
        return max(total_budget - spend_today, 0.0)

    # ─── reports (async) ──────────────────────────────────────────────────

    async def create_campaign_report(self, start: date, end: date) -> str:
        """Kick off an async Sponsored Products campaign report. Returns
        the reportId for polling."""
        data = await self._request(
            "POST", "/reporting/reports",
            json_body={
                "name": f"davinci_marketing_{_fmt_date(start)}_{_fmt_date(end)}",
                "startDate": _fmt_date(start),
                "endDate": _fmt_date(end),
                "configuration": {
                    "adProduct": "SPONSORED_PRODUCTS",
                    "groupBy": ["campaign"],
                    "columns": [
                        "campaignId",
                        "campaignName",
                        "impressions",
                        "clicks",
                        "cost",
                        "sales14d",
                    ],
                    "reportTypeId": "spCampaigns",
                    "timeUnit": "SUMMARY",
                    "format": "GZIP_JSON",
                },
            },
            accept="application/vnd.createasyncreportrequest.v3+json",
        )
        rid = str(data.get("reportId") or "")
        if not rid:
            raise AmazonAdsError(500, "no_report_id", f"unexpected response: {data}", "/reporting/reports")
        return rid

    async def wait_for_report(self, report_id: str) -> str:
        """Poll until COMPLETED; returns the signed download URL. Raises
        AmazonAdsError on FAILED or timeout."""
        elapsed = 0.0
        while elapsed < _POLL_TIMEOUT:
            data = await self._request("GET", f"/reporting/reports/{report_id}")
            status = str(data.get("status") or "").upper()
            if status == "COMPLETED":
                url = data.get("url") or data.get("location") or ""
                if not url:
                    raise AmazonAdsError(500, "no_report_url", "completed report missing URL", report_id)
                return str(url)
            if status in ("FAILED", "CANCELLED"):
                raise AmazonAdsError(500, status.lower(), f"report ended {status}: {data}", report_id)
            await asyncio.sleep(_POLL_INTERVAL)
            elapsed += _POLL_INTERVAL
        raise AmazonAdsError(408, "report_timeout", f"report still {status} after {_POLL_TIMEOUT}s", report_id)

    async def download_report(self, url: str) -> list[dict]:
        """GZIP'd JSON download. The signed URL is unauthenticated — no
        bearer token needed."""
        async with httpx.AsyncClient(timeout=60.0) as c:
            r = await c.get(url)
            r.raise_for_status()
            raw = r.content
        try:
            decompressed = gzip.decompress(raw)
        except OSError:
            # Not actually gzipped (small reports sometimes skip compression).
            decompressed = raw
        return json.loads(decompressed)

    async def get_campaign_performance(
        self, start: date, end: date
    ) -> list[AmazonCampaign]:
        """End-to-end: list campaigns (for state + budget), create+wait+
        download the report (for spend/impressions/sales). Returns a flat
        list joining both."""
        campaigns_meta = await self.list_campaigns()
        by_id = {str(c.get("campaignId")): c for c in campaigns_meta}
        report_id = await self.create_campaign_report(start, end)
        url = await self.wait_for_report(report_id)
        rows = await self.download_report(url)
        out: list[AmazonCampaign] = []
        for row in rows:
            cid = str(row.get("campaignId") or "")
            meta = by_id.get(cid, {})
            spend = _safe_float(row.get("cost"))
            sales = _safe_float(row.get("sales14d"))
            acos = round(spend / sales * 100, 2) if sales > 0 else None
            budget = (meta.get("budget") or {}).get("budget")
            out.append(
                AmazonCampaign(
                    campaign_id=cid,
                    name=str(row.get("campaignName") or meta.get("name") or ""),
                    state=str(meta.get("state") or "unknown").lower(),
                    daily_budget=_safe_float(budget) if budget is not None else None,
                    spend=spend,
                    impressions=_safe_int(row.get("impressions")),
                    clicks=_safe_int(row.get("clicks")),
                    sales_14d=sales,
                    acos=acos,
                )
            )
        # Include campaigns that didn't appear in the report (no spend in
        # window) so the dashboard still shows them with zeros.
        seen = {c.campaign_id for c in out}
        for cid, meta in by_id.items():
            if cid in seen:
                continue
            budget = (meta.get("budget") or {}).get("budget")
            out.append(
                AmazonCampaign(
                    campaign_id=cid,
                    name=str(meta.get("name") or ""),
                    state=str(meta.get("state") or "unknown").lower(),
                    daily_budget=_safe_float(budget) if budget is not None else None,
                    spend=0.0, impressions=0, clicks=0, sales_14d=0.0, acos=None,
                )
            )
        return out


def _safe_float(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _safe_int(v: Any) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0
