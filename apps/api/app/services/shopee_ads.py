"""Shopee Ads API client — Marketing module's data source for live Shopee data.

**Composition over inheritance**: this wraps an existing `ShopeeClient`
instance instead of subclassing it. The parent class is responsible for
stock/price/order syncs that production depends on; we don't want any
risk of an Ads-only change leaking back into those flows. The wrapper
uses `ShopeeClient._request` (already-handled HMAC + token refresh +
the Redis lock that prevents concurrent token rotation) without touching
its public surface.

Date format: Shopee Ads uses DD-MM-YYYY (NOT ISO). `_fmt_date` converts.

Rate limit policy: **fail fast**. The earlier version retried 3× with
backoff, which only deepened the partner-wide throttle ban. Now any
rate-limit response raises `ShopeeAdsRateLimit` immediately so the
orchestrator can set a global Redis cooldown and skip Shopee entirely
for an hour. This is the only thing that keeps the partner-id from
getting suspended.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import structlog

from app.services.marketplaces.shopee import ShopeeClient

logger = structlog.get_logger()

# Error codes that mean Shopee is throttling us — caller MUST treat as
# fatal-for-this-cycle and not retry. The global cooldown is set by
# the orchestrator (services/marketing/shopee_sync.py) on the first hit.
_RATE_LIMIT_CODES = frozenset({
    "ads_rate_limit_total_api",
    "ads.rate_limit.exceed_api",
    "error_rate_limit_total_api",
    "error_rate_limit",
})


class ShopeeAdsError(RuntimeError):
    """Wraps a non-200 Shopee Ads response. `code` mirrors body.error so
    callers can branch on `error_permission_denied`, `ads.rate_limit.exceed_api`,
    etc., without parsing strings."""

    def __init__(self, code: str, message: str, path: str):
        super().__init__(f"{code}: {message} (path={path})")
        self.code = code
        self.message = message
        self.path = path


class ShopeeAdsRateLimit(ShopeeAdsError):
    """Subclass for the per-endpoint Ads throttle (`ads_rate_limit_total_api`
    and friends). Orchestrators catch this specifically to fall back to
    cached balance instead of incrementing consecutive_errors — the
    request will simply be retried next cron tick."""


@dataclass(slots=True)
class DailyMetric:
    day: date
    impressions: int
    clicks: int
    spend: float
    revenue: float  # direct_gmv + broad_gmv
    orders: int
    acos: float | None


@dataclass(slots=True)
class HourlyMetric:
    hour: int  # 0–23 in Shopee's local timezone (UTC+8 / GMT+8 — Shopee BR uses GMT-3? confirm with first response)
    impressions: int
    clicks: int
    spend: float
    revenue: float
    acos: float | None


@dataclass(slots=True)
class CampaignInfo:
    campaign_id: int
    name: str
    status: str  # "ongoing" | "paused" | "ended" | "scheduled"
    daily_budget: float | None
    total_budget: float | None
    bidding_method: str | None
    item_id: int | None


@dataclass(slots=True)
class CampaignPerformance:
    campaign_id: int
    day: date
    impressions: int
    clicks: int
    spend: float
    revenue: float
    orders: int
    acos: float | None


def _fmt_date(d: date) -> str:
    """Shopee Ads expects DD-MM-YYYY; isoformat would silently return empty
    rows."""
    return d.strftime("%d-%m-%Y")


def _gmv(row: dict) -> float:
    """Direct + broad GMV. `direct_gmv` is purchases of the exact ad item;
    `broad_gmv` is the same shop's other items the click attributed to. Both
    count toward our ACOS denominator (matches Shopee Ads Center's headline
    "Sales" number)."""
    return float(row.get("direct_gmv") or 0) + float(row.get("broad_gmv") or 0)


def _orders(row: dict) -> int:
    return int(row.get("direct_orders") or row.get("direct_conversions") or 0) + int(
        row.get("broad_orders") or 0
    )


def _acos(spend: float, revenue: float) -> float | None:
    if revenue <= 0:
        return None
    return round(spend / revenue * 100, 2)


class ShopeeAdsClient:
    """Ads layer wrapping an existing `ShopeeClient` via composition.

    Construct with `ShopeeAdsClient(shopee_client)` — the wrapped client
    is responsible for HMAC signing + token refresh. We don't subclass
    so stock/price flows that share the same ShopeeClient instance can't
    be affected by anything done here.

    Fail-fast: any rate-limit response raises `ShopeeAdsRateLimit`
    immediately. Retry is the orchestrator's call, and the current
    strategy is to NOT retry — set a 1h Redis cooldown and try again
    next cron tick (5 min later, after cooldown).
    """

    def __init__(self, client: ShopeeClient):
        self._client = client

    @property
    def shop_id(self) -> int:
        return self._client.shop_id

    async def _ads_post(self, path: str, body: dict) -> dict:
        """POST helper. Parses body errors and raises:
          - `ShopeeAdsRateLimit` when the response carries a known
            rate-limit code (in body.error OR HTTP 429),
          - `ShopeeAdsError` for any other Shopee-side failure.
        Returns the `response` payload on success."""
        r = await self._client._request("POST", path, json=body)
        if r.status_code == 429:
            try:
                body_json = r.json() or {}
                err_code = str(body_json.get("error") or "error_rate_limit")
                err_msg = str(body_json.get("message") or r.text[:200])
            except Exception:  # noqa: BLE001
                err_code, err_msg = "error_rate_limit", r.text[:200]
            raise ShopeeAdsRateLimit(err_code, err_msg, path)
        if r.status_code >= 400:
            raise ShopeeAdsError(
                "http_error", f"status={r.status_code} body={r.text[:200]}", path
            )
        body_json = r.json() or {}
        err = body_json.get("error")
        if err:
            code = str(err)
            msg = str(body_json.get("message") or "")
            if code in _RATE_LIMIT_CODES:
                raise ShopeeAdsRateLimit(code, msg, path)
            raise ShopeeAdsError(code, msg, path)
        return body_json.get("response") or body_json

    # ─── 1. credit balance ───────────────────────────────────────────────

    async def get_balance(self) -> float:
        """Shop-level Ads credit. Returns 0.0 if Shopee returns null
        (account exists but never funded)."""
        data = await self._ads_post("/api/v2/ads/get_total_balance", {})
        return float(data.get("total_balance") or 0)

    # ─── 2. campaigns: list ids + fetch settings ─────────────────────────

    async def list_campaign_ids(
        self, *, ad_type: str = "all", offset: int = 0, limit: int = 50
    ) -> list[int]:
        """Paginated list of product-level campaign ids. `ad_type=all` covers
        both manual and auto product ads."""
        data = await self._ads_post(
            "/api/v2/ads/get_product_level_campaign_id_list",
            {"ad_type": ad_type, "offset": offset, "limit": limit},
        )
        ids = data.get("campaign_id_list") or []
        return [int(x) for x in ids]

    async def get_campaign_settings(self, campaign_ids: list[int]) -> list[CampaignInfo]:
        """Returns name/status/budget/bid for the requested campaign ids.
        Shopee returns at most 100 per call; we chunk to be safe."""
        out: list[CampaignInfo] = []
        for chunk_start in range(0, len(campaign_ids), 100):
            chunk = campaign_ids[chunk_start : chunk_start + 100]
            if not chunk:
                continue
            data = await self._ads_post(
                "/api/v2/ads/get_product_level_campaign_setting_info",
                {"campaign_id_list": chunk, "info_type": 1},
            )
            for row in data.get("campaign_list") or []:
                out.append(
                    CampaignInfo(
                        campaign_id=int(row.get("campaign_id") or 0),
                        name=str(row.get("campaign_name") or row.get("name") or ""),
                        status=str(row.get("campaign_status") or row.get("status") or "unknown"),
                        daily_budget=(
                            float(row["daily_budget"]) if row.get("daily_budget") is not None else None
                        ),
                        total_budget=(
                            float(row["total_budget"]) if row.get("total_budget") is not None else None
                        ),
                        bidding_method=row.get("bidding_method"),
                        item_id=int(row["item_id"]) if row.get("item_id") is not None else None,
                    )
                )
        return out

    # ─── 3. daily performance (shop-level) ───────────────────────────────

    async def get_daily_performance(self, start: date, end: date) -> list[DailyMetric]:
        """Aggregated daily numbers across ALL the shop's CPC ads. One row
        per day in [start, end]."""
        data = await self._ads_post(
            "/api/v2/ads/get_all_cpc_ads_daily_performance",
            {"start_date": _fmt_date(start), "end_date": _fmt_date(end)},
        )
        rows = data.get("performance_list") or data.get("daily_list") or []
        out: list[DailyMetric] = []
        for r in rows:
            spend = float(r.get("expense") or 0)
            revenue = _gmv(r)
            day_str = str(r.get("date") or "")
            try:
                # Shopee returns "DD-MM-YYYY" matching what we sent.
                dd, mm, yy = day_str.split("-")
                d = date(int(yy), int(mm), int(dd))
            except (ValueError, KeyError):
                continue
            out.append(
                DailyMetric(
                    day=d,
                    impressions=int(r.get("impression") or 0),
                    clicks=int(r.get("clicks") or 0),
                    spend=spend,
                    revenue=revenue,
                    orders=_orders(r),
                    acos=_acos(spend, revenue),
                )
            )
        return out

    # ─── 4. hourly performance (for ACOS heatmap) ────────────────────────

    async def get_hourly_performance(self, on: date) -> list[HourlyMetric]:
        """24-row breakdown for a single day. Hour values are 0..23 in
        Shopee's reporting timezone (shop-localized)."""
        data = await self._ads_post(
            "/api/v2/ads/get_all_cpc_ads_hourly_performance",
            {"date": _fmt_date(on)},
        )
        rows = data.get("performance_list") or data.get("hourly_list") or []
        out: list[HourlyMetric] = []
        for r in rows:
            spend = float(r.get("expense") or 0)
            revenue = _gmv(r)
            out.append(
                HourlyMetric(
                    hour=int(r.get("hour") or 0),
                    impressions=int(r.get("impression") or 0),
                    clicks=int(r.get("clicks") or 0),
                    spend=spend,
                    revenue=revenue,
                    acos=_acos(spend, revenue),
                )
            )
        return out

    # ─── 5. campaign performance ─────────────────────────────────────────

    async def get_campaign_performance(
        self, campaign_ids: list[int], start: date, end: date
    ) -> list[CampaignPerformance]:
        """Per-campaign × per-day metrics. Shopee allows comma-separated
        ids in one call; we still chunk because the docs cap the list at
        100."""
        out: list[CampaignPerformance] = []
        for chunk_start in range(0, len(campaign_ids), 100):
            chunk = campaign_ids[chunk_start : chunk_start + 100]
            if not chunk:
                continue
            data = await self._ads_post(
                "/api/v2/ads/get_product_campaign_daily_performance",
                {
                    "start_date": _fmt_date(start),
                    "end_date": _fmt_date(end),
                    "campaign_id_list": ",".join(str(c) for c in chunk),
                },
            )
            for r in data.get("campaign_list") or []:
                cid = int(r.get("campaign_id") or 0)
                for day_row in r.get("performance_list") or []:
                    day_str = str(day_row.get("date") or "")
                    try:
                        dd, mm, yy = day_str.split("-")
                        d = date(int(yy), int(mm), int(dd))
                    except ValueError:
                        continue
                    spend = float(day_row.get("expense") or 0)
                    revenue = _gmv(day_row)
                    out.append(
                        CampaignPerformance(
                            campaign_id=cid,
                            day=d,
                            impressions=int(day_row.get("impression") or 0),
                            clicks=int(day_row.get("clicks") or 0),
                            spend=spend,
                            revenue=revenue,
                            orders=_orders(day_row),
                            acos=_acos(spend, revenue),
                        )
                    )
        return out

    # ─── 6. edit campaign (agent actions) ────────────────────────────────

    async def edit_campaign(
        self,
        campaign_id: int,
        *,
        status: str | None = None,
        daily_budget: float | None = None,
    ) -> dict:
        """Change `status` ('ongoing' | 'paused') and/or `daily_budget`.
        Shopee rejects calls with no actionable field, so we require at
        least one."""
        if status is None and daily_budget is None:
            raise ValueError("edit_campaign requires status or daily_budget")
        body: dict = {"campaign_id": int(campaign_id)}
        if status is not None:
            body["status"] = status
        if daily_budget is not None:
            body["daily_budget"] = float(daily_budget)
        return await self._ads_post("/api/v2/ads/edit_manual_product_ads", body)
