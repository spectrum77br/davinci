"""Shopee Ads API client — Marketing module's data source for live Shopee data.

Reuses `ShopeeClient`'s HMAC signing + token refresh (subclassing it gets us
`_request`, `refresh`, the encrypted-creds shape, and the Redis-locked
token rotation for free). All the methods here just wrap the
`/api/v2/ads/*` endpoints with typed return values and apply the rate
limit Shopee enforces (1 req/s, soft).

Date format: Shopee Ads uses DD-MM-YYYY (NOT ISO). The helpers `_fmt_date`
and `_parse_date` convert at the boundary so the rest of the app keeps
dealing with `datetime.date`.

Errors: every Shopee response carries `error` + `message`. We surface those
as `ShopeeAdsError` so the orchestrator can decide between (a) silent fallback
to last-saved DB row, (b) logging the issue + Telegram alert, or (c) hard
failing for the operator. `error_permission_denied` is the most common one
for accounts that haven't enabled Ads — we treat it as a soft error.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date

import structlog

from app.services.marketplaces.shopee import ShopeeClient

logger = structlog.get_logger()

# Soft rate limit Shopee enforces for Ads endpoints. 1.0s was too aggressive
# in prod — `get_total_balance` specifically returns `ads_rate_limit_total_api`
# on every call when fired in a tight loop across 13 shops, even with the
# 1s breather. Bumping to 3s spreads the burst enough that 13 sequential
# syncs (about 40s of total sleep) stay under Shopee's "balance" sub-quota.
_RATE_LIMIT_SECONDS = 3.0

# When a rate-limit error specifically targets the balance endpoint, retry
# with exponential backoff. Per-error code so we don't muddle generic 5xx
# failures with this very chatty Shopee-side throttle.
_RATE_LIMIT_RETRY_CODES = frozenset({
    "ads_rate_limit_total_api",
    "ads.rate_limit.exceed_api",
    "error_rate_limit_total_api",
})
_RATE_LIMIT_MAX_RETRIES = 3
_RATE_LIMIT_BACKOFF_BASE = 5.0  # seconds: 5, 10, 20


class ShopeeAdsError(RuntimeError):
    """Wraps a non-200 Shopee Ads response. `code` mirrors body.error so
    callers can branch on `error_permission_denied`, `ads.rate_limit.exceed_api`,
    etc., without parsing strings."""

    def __init__(self, code: str, message: str, path: str):
        super().__init__(f"{code}: {message} (path={path})")
        self.code = code
        self.message = message
        self.path = path


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


class ShopeeAdsClient(ShopeeClient):
    """Thin Ads layer on top of ShopeeClient. Each method makes ONE Ads call
    + sleeps `_RATE_LIMIT_SECONDS` after it. The parent class handles HMAC
    signing, token refresh, base URL, and shop_id query injection."""

    async def _ads_post(self, path: str, body: dict) -> dict:
        """POST helper that raises ShopeeAdsError on any body.error and
        otherwise returns the `response` payload (Shopee Ads wraps results
        in `{error, message, response}`).

        Auto-retries `ads_rate_limit_total_api` (the per-endpoint throttle
        Shopee Ads enforces on `get_total_balance` and friends) with
        exponential backoff. Without this, the very first run across 13
        shops fails 100% because the throttle kicks in before the loop
        finishes."""
        attempt = 0
        last_err: ShopeeAdsError | None = None
        while attempt <= _RATE_LIMIT_MAX_RETRIES:
            r = await self._request("POST", path, json=body)
            if r.status_code >= 400:
                raise ShopeeAdsError(
                    "http_error", f"status={r.status_code} body={r.text[:200]}", path
                )
            body_json = r.json() or {}
            err = body_json.get("error")
            if err:
                code = str(err)
                msg = str(body_json.get("message") or "")
                if code in _RATE_LIMIT_RETRY_CODES and attempt < _RATE_LIMIT_MAX_RETRIES:
                    delay = _RATE_LIMIT_BACKOFF_BASE * (2 ** attempt)
                    logger.warning(
                        "shopee_ads_rate_limited",
                        path=path, code=code, attempt=attempt + 1, sleep=delay,
                    )
                    await asyncio.sleep(delay)
                    attempt += 1
                    last_err = ShopeeAdsError(code, msg, path)
                    continue
                raise ShopeeAdsError(code, msg, path)
            await asyncio.sleep(_RATE_LIMIT_SECONDS)
            return body_json.get("response") or body_json
        # All retries exhausted — surface the last error
        raise last_err if last_err else ShopeeAdsError("rate_limit_exhausted", "", path)

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
