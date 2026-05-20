"""Mercado Livre Product Ads API client — Marketing module's live ML data.

Subclasses `MercadoLivreClient` to reuse its OAuth refresh + 401/429 retry
logic. The /advertising/* and /marketplace/advertising/* endpoints need
the `Api-Version: 2` header and require the underlying OAuth token to
carry the `advertising` / `product_ads` scope. Tokens without that scope
hit 403 on the first ad call — we raise `MLAdsScopeError` so the
orchestrator can mark the integration as needing re-auth without
crashing the rest of the marketing pull.

ML does NOT expose a credit balance like Shopee. The closest concept is
"daily budget remaining" — total active campaign budgets minus today's
spend. The orchestrator surfaces that as `credit_balance` so the UI's
credit column behaves consistently across platforms.

Date format: ML expects YYYY-MM-DD (ISO). Brazil site_id is `MLB`.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx
import structlog

from app.services.marketplaces.ml import MercadoLivreClient

logger = structlog.get_logger()

ML_SITE_ID_BR = "MLB"

# Soft rate limit: ML allows up to ~10 req/s but the marketing sync only
# needs a handful of calls per shop so a 0.2s breath after each request
# is plenty and stays well under any per-app cap.
_RATE_LIMIT_SECONDS = 0.2


class MLAdsError(RuntimeError):
    """Wraps an ML Ads HTTP error with `status` + `code` so the
    orchestrator can branch on permission/scope vs transient."""

    def __init__(self, status: int, code: str, message: str, path: str):
        super().__init__(f"{status} {code}: {message} (path={path})")
        self.status = status
        self.code = code
        self.message = message
        self.path = path


class MLAdsScopeError(MLAdsError):
    """Raised when ML returns 403 on an /advertising/* endpoint —
    indicates the integration's OAuth token lacks the advertising
    scope. Caller marks the Integration as needing re-auth."""


@dataclass(slots=True)
class MLCampaign:
    campaign_id: str
    name: str
    status: str  # "active" | "paused" | "ended"
    daily_budget: float | None
    spend: float
    impressions: int
    clicks: int
    acos: float | None  # ML's own ACOS using its attributed sales


@dataclass(slots=True)
class MLDailyMetric:
    day: date
    spend: float
    impressions: int
    clicks: int
    revenue: float
    acos: float | None


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


class MLAdsClient(MercadoLivreClient):
    """Adds /advertising/* + /marketplace/advertising/* endpoints to the
    existing ML client. Caches `advertiser_id` on the instance so
    subsequent calls in the same sync don't re-discover it."""

    def __init__(self, creds: dict, on_token_refresh=None):
        super().__init__(creds, on_token_refresh=on_token_refresh)
        self._advertiser_id: str | None = None

    async def _ads_request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json: Any = None,
    ) -> dict:
        """Wraps `_request` with the Api-Version header injection + ML Ads
        error shape parsing. Raises MLAdsScopeError on 403 so the
        orchestrator can distinguish "missing scope" from "transient"."""
        # _request handles auth header + token refresh; we attach
        # Api-Version: 2 via params since the parent doesn't accept extra
        # headers per-call. ML accepts both — `api_version=2` as a query
        # param has the same effect on Ads endpoints.
        merged_params = dict(params or {})

        # ML's parent _request retries 429/502/503/504 once; we wrap the
        # final response only.
        # NB: we mutate the parent's headers via a sub-request done with
        # httpx directly so the Api-Version header is set canonically.
        # (Parent's _request didn't expose headers kwarg.)
        if self._expired():
            await self.refresh()
        from app.services.marketplaces.ml import ML_API_BASE  # avoid cycle

        url = f"{ML_API_BASE}{path}"
        # Api-Version: 1 is the version /marketplace/advertising/* and the
        # newer /advertising/advertisers/{id}/product_ads/campaigns endpoints
        # accept. v2 returned 400 ("Metrics X is not valid") on the
        # campaigns/search path — ML rejected the request entirely with
        # that header, regardless of the params we sent.
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
            "Api-Version": "1",
        }
        delay = 1.0
        last_resp: httpx.Response | None = None
        for attempt in range(3):
            async with httpx.AsyncClient(timeout=30.0) as c:
                r = await c.request(method, url, headers=headers, params=merged_params, json=json)
            last_resp = r
            if r.status_code == 401 and attempt == 0:
                await self.refresh()
                headers["Authorization"] = f"Bearer {self.access_token}"
                continue
            if r.status_code in (429, 502, 503, 504):
                logger.warning("ml_ads_retry", attempt=attempt + 1, status=r.status_code, path=path)
                await asyncio.sleep(delay)
                delay *= 2
                continue
            break
        await asyncio.sleep(_RATE_LIMIT_SECONDS)
        r = last_resp  # type: ignore[assignment]
        if r.status_code == 403:
            raise MLAdsScopeError(
                403, "scope_missing",
                "token lacks 'advertising' scope — reconnect ML",
                path,
            )
        if r.status_code >= 400:
            body = r.text[:300]
            try:
                jb = r.json()
                code = str(jb.get("error") or jb.get("status") or "http_error")
                msg = str(jb.get("message") or body)
            except Exception:  # noqa: BLE001
                code = "http_error"
                msg = body
            raise MLAdsError(r.status_code, code, msg, path)
        return r.json() or {}

    # ─── advertiser discovery ─────────────────────────────────────────────

    async def get_advertiser_id(self) -> str:
        """First call in any ML Ads flow — resolves the advertiser_id the
        rest of the endpoints need. Cached on the instance because every
        sync makes 3-5 calls and they all reuse the same id."""
        if self._advertiser_id is not None:
            return self._advertiser_id
        data = await self._ads_request(
            "GET", "/advertising/advertisers", params={"product_id": "PADS"}
        )
        advertisers = data.get("advertisers") or []
        if not advertisers:
            raise MLAdsError(
                404, "no_advertiser",
                "this ML account has no PADS advertiser — Product Ads not enabled",
                "/advertising/advertisers",
            )
        self._advertiser_id = str(advertisers[0].get("advertiser_id") or advertisers[0].get("id") or "")
        if not self._advertiser_id:
            raise MLAdsError(
                500, "bad_advertiser_response",
                f"advertiser shape unexpected: {advertisers[0]}",
                "/advertising/advertisers",
            )
        return self._advertiser_id

    # ─── campaigns + metrics ──────────────────────────────────────────────

    async def list_campaigns_metadata(
        self, *, limit: int = 50
    ) -> list[dict]:
        """List campaign metadata (id/name/status/budget) — NO metrics.
        Empirically this is the only thing `/marketplace/advertising/.../
        product_ads/campaigns/search` accepts; any `metrics=` param trips
        a 400. Metrics get pulled from a separate endpoint by
        `fetch_campaign_metrics`."""
        advertiser_id = await self.get_advertiser_id()
        path = (
            f"/marketplace/advertising/{ML_SITE_ID_BR}/advertisers/"
            f"{advertiser_id}/product_ads/campaigns/search"
        )
        out: list[dict] = []
        offset = 0
        while True:
            data = await self._ads_request(
                "GET", path, params={"limit": limit, "offset": offset},
            )
            results = data.get("results") or data.get("campaigns") or []
            if not results:
                break
            out.extend(results)
            if len(results) < limit:
                break
            offset += limit
            if offset > 1000:
                logger.warning("ml_ads_pagination_cap", path=path, offset=offset)
                break
        return out

    async def fetch_campaign_metrics(
        self, *, date_from: date, date_to: date
    ) -> dict[str, dict]:
        """Pull per-campaign metrics from `/advertising/advertisers/{adv}
        /product_ads/campaigns`. The key was passing `metrics=...` with
        ML's exact vocabulary:

          prints          → impressions
          clicks          → clicks
          cost            → spend (R$)
          direct_amount   → directly-attributed sales
          indirect_amount → indirectly-attributed sales
          total_amount    → direct + indirect (the headline "revenue" ML uses)
          acos            → ML's own ACOS calc

        Without the `metrics=` query the endpoint returns config-only
        (acos_target, roas_target, budget) without performance numbers.
        Returns a dict keyed by campaign_id with spend/impressions/clicks/
        attributed_revenue. Falls back to {} on any error so the caller
        can still persist campaigns with zero metrics."""
        advertiser_id = await self.get_advertiser_id()
        path = f"/advertising/advertisers/{advertiser_id}/product_ads/campaigns"
        out: dict[str, dict] = {}
        try:
            data = await self._ads_request(
                "GET", path,
                params={
                    "metrics": "prints,clicks,cost,direct_amount,indirect_amount,total_amount,acos",
                    "date_from": date_from.isoformat(),
                    "date_to": date_to.isoformat(),
                },
            )
        except MLAdsError as e:
            logger.warning(
                "ml_ads_campaign_metrics_endpoint_failed",
                path=path, code=e.code, status=e.status,
            )
            return out
        rows = data.get("results") or data.get("campaigns") or []
        for c in rows:
            cid = str(c.get("id") or c.get("campaign_id") or "")
            if not cid:
                continue
            m = c.get("metrics") or {}
            out[cid] = {
                "spend": _safe_float(m.get("cost")),
                "impressions": _safe_int(m.get("prints")),
                "clicks": _safe_int(m.get("clicks")),
                # "total_amount" = direct + indirect attributed sales,
                # which is the closest analogue to "campaign revenue"
                # that ML reports. We persist this in MarketingCampaign.revenue
                # for the per-campaign view; account-level still uses Bling.
                "attributed_revenue": _safe_float(m.get("total_amount")),
                "direct_revenue": _safe_float(m.get("direct_amount")),
                "ml_acos": (float(m["acos"]) if m.get("acos") is not None else None),
            }
        return out

    async def list_campaigns_with_metrics(
        self, *, date_from: date, date_to: date, limit: int = 50
    ) -> list[MLCampaign]:
        """Returns campaigns enriched with metrics. Two-step pull because
        the campaigns/search endpoint refuses to embed metrics on
        Api-Version 1 — see `list_campaigns_metadata` + `fetch_campaign_metrics`."""
        meta = await self.list_campaigns_metadata(limit=limit)
        metrics_by_id = await self.fetch_campaign_metrics(
            date_from=date_from, date_to=date_to
        )
        out: list[MLCampaign] = []
        for c in meta:
            cid = str(c.get("id") or c.get("campaign_id") or "")
            m = metrics_by_id.get(cid, {})
            spend = float(m.get("spend") or 0)
            revenue = float(m.get("attributed_revenue") or 0)
            acos = round(spend / revenue * 100, 2) if revenue > 0 else None
            out.append(
                MLCampaign(
                    campaign_id=cid,
                    name=str(c.get("name") or ""),
                    status=str(c.get("status") or "unknown").lower(),
                    daily_budget=(
                        _safe_float(c.get("daily_budget"))
                        if c.get("daily_budget") is not None
                        else None
                    ),
                    spend=spend,
                    impressions=int(m.get("impressions") or 0),
                    clicks=int(m.get("clicks") or 0),
                    acos=acos,
                )
            )
        return out

    # ─── daily aggregates (no native endpoint; derive from campaigns) ─────

    async def get_daily_performance(
        self, start: date, end: date
    ) -> tuple[list[MLCampaign], MLDailyMetric]:
        """ML doesn't have a per-day shop-level rollup like Shopee's
        get_all_cpc_ads_daily_performance. We pull campaigns with the
        window's totals and synthesise ONE day-bucket at `end` for the
        whole period. The orchestrator stores that as a single
        MarketingMetric row (intensity=0 sentinel)."""
        campaigns = await self.list_campaigns_with_metrics(
            date_from=start, date_to=end
        )
        spend = sum(c.spend for c in campaigns)
        impressions = sum(c.impressions for c in campaigns)
        clicks = sum(c.clicks for c in campaigns)
        # ML-attributed revenue total (not authoritative — orchestrator
        # replaces with Bling). Kept for fallback when Bling isn't wired.
        revenue = 0.0
        for c in campaigns:
            if c.acos and c.acos > 0:
                revenue += c.spend / (c.acos / 100)
        acos = round(spend / revenue * 100, 2) if revenue > 0 else None
        return campaigns, MLDailyMetric(
            day=end, spend=spend, impressions=impressions,
            clicks=clicks, revenue=revenue, acos=acos,
        )

    # ─── credit (derived: remaining daily budget) ─────────────────────────

    async def get_remaining_daily_budget(
        self, *, campaigns: list[MLCampaign] | None = None
    ) -> float:
        """ML has no credit pot. The dashboard's `credit` column shows
        "today's remaining daily budget" instead — total active budgets
        minus today's spend. Accepts a pre-fetched campaign list to save
        a round-trip when the caller already pulled them."""
        if campaigns is None:
            campaigns, _ = await self.get_daily_performance(date.today(), date.today())
        total_budget = sum(
            (c.daily_budget or 0) for c in campaigns if c.status == "active"
        )
        spend_today = sum(c.spend for c in campaigns if c.status == "active")
        return max(total_budget - spend_today, 0.0)

    # ─── edit campaign (agent actions) ────────────────────────────────────

    async def edit_campaign(
        self,
        campaign_id: str,
        *,
        status: str | None = None,
        daily_budget: float | None = None,
    ) -> dict:
        """Update campaign status ('active' | 'paused') and/or daily
        budget. Uses PUT on the campaign resource per ML Product Ads docs."""
        advertiser_id = await self.get_advertiser_id()
        body: dict = {}
        if status is not None:
            body["status"] = status
        if daily_budget is not None:
            body["daily_budget"] = float(daily_budget)
        if not body:
            raise ValueError("edit_campaign requires status or daily_budget")
        return await self._ads_request(
            "PUT",
            f"/advertising/advertisers/{advertiser_id}/product_ads/campaigns/{campaign_id}",
            json=body,
        )
