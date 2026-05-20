"""Pull live Shopee Ads numbers into the marketing_* tables.

For each Shopee Integration row we:
  1. decrypt creds → make a ShopeeAdsClient (refresh callback writes new
     tokens back so the next call doesn't refresh again).
  2. fetch balance + last-7d daily + today's hourly + all campaign settings
     + last-7d per-campaign performance.
  3. upsert one MarketingAccount per integration (department='geral' —
     Shopee Ads doesn't model departments, see ShopeeAdsService.md for the
     architectural decision).
  4. upsert MarketingMetric rows: one per hour for today (heatmap) + one
     per day for the 7-day window (daily aggregates).
  5. upsert MarketingCampaign rows by (account_id, external_id) so the
     Campanhas tab reflects ongoing/paused state + per-campaign spend/ACOS.
  6. fire a Telegram alert if balance fell below `_CREDIT_ALERT_THRESHOLD`.

Failure mode: if Shopee returns `error_permission_denied` (Ads not enabled
on the shop) we mark `last_sync_error` on the account and return so the
worker doesn't keep retrying. Other errors propagate so the worker logs
them and the operator can investigate.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.integration import Integration
from app.models.marketing import MarketingAccount, MarketingCampaign, MarketingMetric
from app.security.cipher import decrypt_json, encrypt_json
from app.services.marketing.alerts import (
    notify_high_acos,
    notify_low_credit,
    record_sync_failure,
    record_sync_success,
)
from app.services.marketing.bling_revenue import BlingRevenue, get_bling_revenue
from app.services.shopee_ads import (
    CampaignInfo,
    CampaignPerformance,
    DailyMetric,
    HourlyMetric,
    ShopeeAdsClient,
    ShopeeAdsError,
)

logger = structlog.get_logger()

# Shopee credit balance below which we Telegram the operator. R$50 picked
# from the spec; the real "low" threshold varies per shop's daily burn, so
# this is a backstop — the per-account days_remaining alert in the
# /credit-alerts endpoint is the primary signal.
_CREDIT_ALERT_THRESHOLD = 50.0

# Days of daily data we pull per sync. 7 is enough for the 7d table; 30d
# table will fall back to the persisted MarketingMetric history.
_DAILY_LOOKBACK_DAYS = 7


async def sync_shopee_integration(
    session: AsyncSession,
    integration_id: UUID,
    *,
    pull_hourly: bool = True,
    pull_campaigns: bool = True,
) -> dict[str, Any]:
    """Sync one Shopee integration. Returns a small dict the caller can log
    or surface in the manual-trigger response. Caller is responsible for
    the transaction lifecycle (commit happens here)."""
    integration = await session.get(Integration, integration_id)
    if integration is None:
        return {"status": "error", "code": "integration_not_found"}
    if integration.platform.value != "shopee":
        return {"status": "skipped", "reason": "not_shopee", "platform": integration.platform.value}

    creds = decrypt_json(integration.credentials)

    async def _persist_refreshed_creds(new_creds: dict) -> None:
        # ShopeeClient calls this after a successful refresh — bake new
        # access/refresh tokens back into the encrypted blob so the next
        # request doesn't refresh again.
        integration.credentials = encrypt_json(new_creds)
        expires_at = new_creds.get("expires_at")
        if expires_at:
            integration.token_expires_at = datetime.fromtimestamp(int(expires_at), tz=UTC)
        await session.flush()

    client = ShopeeAdsClient(creds, on_token_refresh=_persist_refreshed_creds)

    today = datetime.now(UTC).date()
    start_day = today - timedelta(days=_DAILY_LOOKBACK_DAYS - 1)

    try:
        balance = await client.get_balance()
        daily = await client.get_daily_performance(start_day, today)
        hourly = await client.get_hourly_performance(today) if pull_hourly else []
        campaign_ids = await client.list_campaign_ids() if pull_campaigns else []
        campaign_info = (
            await client.get_campaign_settings(campaign_ids) if campaign_ids else []
        )
        campaign_perf = (
            await client.get_campaign_performance(campaign_ids, start_day, today)
            if campaign_ids
            else []
        )
    except ShopeeAdsError as e:
        # Soft error path: persist on the integration so the UI can show
        # "Sem permissão de Ads" without breaking the rest of the dashboard.
        integration.last_error = f"{e.code}: {e.message}"[:500]
        await record_sync_failure(session, integration, code=e.code, message=e.message)
        await session.flush()
        if e.code in {"error_permission_denied", "error_permission"}:
            logger.warning(
                "shopee_ads_permission_denied",
                integration_id=str(integration_id),
                shop_id=int(creds.get("shop_id") or 0),
            )
            await session.commit()
            return {"status": "skipped", "code": e.code, "message": e.message}
        # Re-raise for the cron logger / manual trigger to surface
        raise

    # ─── Bling revenue (authoritative faturamento) ───────────────────────
    # Pull NF-emitida pedidos for the same window so we can replace the
    # Shopee GMV (attributed sales) with what actually shipped. None means
    # Bling isn't wired up or the integration has no loja mapping — we
    # silently fall back to GMV for those rows.
    bling = await get_bling_revenue(session, integration, start=start_day, end=today)
    daily = _apply_bling_revenue(daily, bling)

    # ─── upsert MarketingAccount ─────────────────────────────────────────
    account = await _upsert_account(
        session,
        integration=integration,
        balance=balance,
        daily=daily,
        today=today,
    )

    # ─── hourly heatmap rows for today ───────────────────────────────────
    # Hourly revenue stays GMV-based — Bling has no hourly granularity.
    # The hourly ACOS heatmap is an approximation; the per-day ACOS in the
    # 7d/30d tables is the trustworthy one.
    if hourly:
        await _upsert_hourly_metrics(session, account_id=account.id, today=today, rows=hourly)

    # ─── daily metrics — one row per day in the window ───────────────────
    if daily:
        await _upsert_daily_metrics(session, account_id=account.id, rows=daily)

    # ─── campaigns + per-campaign rollup (last N days) ───────────────────
    # Per-campaign revenue is also GMV-based: Bling doesn't tag pedidos
    # with the originating campaign, so we keep Shopee's attributed sales
    # as the per-campaign signal. The account-level row in the tables uses
    # Bling; the Campanhas grid uses GMV — that asymmetry is intentional.
    perf_by_campaign = _rollup_campaign_perf(campaign_perf)
    if campaign_info:
        await _upsert_campaigns(
            session,
            account_id=account.id,
            shop_balance=balance,
            info_list=campaign_info,
            perf_by_campaign=perf_by_campaign,
        )

    integration.last_error = None
    integration.last_test_ok = True
    integration.last_test_at = datetime.now(UTC)
    await record_sync_success(session, integration)
    await session.commit()

    # ─── post-commit Telegram alerts (best-effort, deduped) ──────────────
    await notify_low_credit(integration, credit=balance, threshold=_CREDIT_ALERT_THRESHOLD)
    # Today's ACOS = today's spend / Bling-revenue-today. Use the
    # patched daily list (where _apply_bling_revenue already replaced
    # revenue + recomputed acos) so the alert matches what the UI shows.
    today_row = next((d for d in daily if d.day == today), None)
    if today_row and today_row.acos is not None:
        await notify_high_acos(integration, today_row.acos, account.acos_target)

    return {
        "status": "ok",
        "account_id": str(account.id),
        "balance": balance,
        "daily_rows": len(daily),
        "hourly_rows": len(hourly),
        "campaigns": len(campaign_info),
        "bling_revenue": bling.total if bling else None,
        "bling_orders": bling.order_count if bling else None,
    }


async def sync_all_shopee_integrations(session: AsyncSession) -> list[dict[str, Any]]:
    """Worker entry point — iterate every Shopee integration opted in to
    the marketing module (`ads_enabled=True`). Each integration is synced
    inside its own try/except so one failure doesn't bring down the rest
    of the cron tick. Integrations without the opt-in are left alone so a
    plain stock/order sync doesn't accidentally burn ads-API quota."""
    rows = (
        await session.execute(
            select(Integration).where(
                and_(
                    Integration.status == "active",
                    Integration.platform == "shopee",  # SQLAlchemy enum coerces
                    Integration.ads_enabled.is_(True),
                )
            )
        )
    ).scalars().all()
    out: list[dict[str, Any]] = []
    for integ in rows:
        try:
            result = await sync_shopee_integration(session, integ.id)
            out.append({"integration_id": str(integ.id), **result})
        except Exception as e:  # noqa: BLE001
            logger.error(
                "shopee_ads_sync_failed",
                integration_id=str(integ.id),
                err=str(e)[:300],
            )
            out.append({"integration_id": str(integ.id), "status": "error", "error": str(e)[:200]})
    return out


# ─── internals ────────────────────────────────────────────────────────────


def _apply_bling_revenue(
    daily: list[DailyMetric], bling: BlingRevenue | None
) -> list[DailyMetric]:
    """Replace each day's revenue with Bling's NF-emitida total for that
    same date. Recomputes ACOS off the new denominator. Days that Bling
    didn't return (no pedidos that day) get revenue=0 and ACOS=None —
    that's the truthful answer for a day with zero faturamento.

    If Bling isn't available the original list is returned unchanged so
    the sync keeps working with platform GMV as a degraded fallback."""
    if bling is None:
        return daily
    patched: list[DailyMetric] = []
    for d in daily:
        bling_rev = bling.by_day.get(d.day, 0.0)
        acos = round(d.spend / bling_rev * 100, 2) if bling_rev > 0 else None
        patched.append(
            DailyMetric(
                day=d.day,
                impressions=d.impressions,
                clicks=d.clicks,
                spend=d.spend,
                revenue=bling_rev,
                orders=d.orders,
                acos=acos,
            )
        )
    return patched


async def _upsert_account(
    session: AsyncSession,
    *,
    integration: Integration,
    balance: float,
    daily: list[DailyMetric],
    today: date,
) -> MarketingAccount:
    """Resolve the (integration_id) MarketingAccount or create one. Each
    Shopee integration owns exactly one MarketingAccount (1 loja = 1
    dept, per the spec); the department comes from `Integration.department`
    when set, else falls back to 'geral'. The mock seed creates
    dept-specific Shopee rows with integration_id=NULL — those stay
    untouched here so /seed and live data can coexist."""
    dept = (integration.department or "geral").lower()
    existing = (
        await session.execute(
            select(MarketingAccount).where(
                MarketingAccount.integration_id == integration.id
            )
        )
    ).scalar_one_or_none()

    today_row = next((d for d in daily if d.day == today), None)

    if existing is None:
        existing = MarketingAccount(
            integration_id=integration.id,
            name=integration.name,
            platform="shopee",
            department=dept,
            acos_target=7.0,
            credit_balance=balance,
            agent_enabled=False,  # off by default — operator opts in
            status="active",
            spend_today=today_row.spend if today_row else 0.0,
            revenue_today=today_row.revenue if today_row else 0.0,
            impressions_today=today_row.impressions if today_row else 0,
        )
        session.add(existing)
        await session.flush()
    else:
        existing.department = dept
        existing.credit_balance = balance
        if today_row:
            existing.spend_today = today_row.spend
            existing.revenue_today = today_row.revenue
            existing.impressions_today = today_row.impressions
        existing.status = "active"
    return existing


async def _upsert_hourly_metrics(
    session: AsyncSession,
    *,
    account_id: UUID,
    today: date,
    rows: list[HourlyMetric],
) -> None:
    """One MarketingMetric row per hour at midnight-UTC + hour. Replaces
    today's existing rows so re-syncs converge to Shopee's authoritative
    numbers (rather than double-counting)."""
    day_start = datetime.combine(today, time.min, tzinfo=UTC)
    day_end = day_start + timedelta(days=1)
    existing = (
        await session.execute(
            select(MarketingMetric).where(
                and_(
                    MarketingMetric.account_id == account_id,
                    MarketingMetric.timestamp >= day_start,
                    MarketingMetric.timestamp < day_end,
                )
            )
        )
    ).scalars().all()
    by_hour = {m.timestamp.hour: m for m in existing}
    for hr in rows:
        ts = day_start + timedelta(hours=hr.hour)
        m = by_hour.get(hr.hour)
        if m is None:
            session.add(
                MarketingMetric(
                    account_id=account_id, timestamp=ts,
                    spend=hr.spend, revenue=hr.revenue,
                    impressions=hr.impressions, clicks=hr.clicks,
                    orders=0, acos=hr.acos, intensity=50,
                )
            )
        else:
            m.spend = hr.spend
            m.revenue = hr.revenue
            m.impressions = hr.impressions
            m.clicks = hr.clicks
            m.acos = hr.acos


async def _upsert_daily_metrics(
    session: AsyncSession,
    *,
    account_id: UUID,
    rows: list[DailyMetric],
) -> None:
    """One MarketingMetric row per day, anchored at noon UTC. We keep these
    separate from the hourly entries (different `intensity` = 0 sentinel)
    so the heatmap query can ignore them; the 7d/30d summary aggregates
    *all* rows so the daily entry is the dominant signal for accounts
    where hourly isn't pulled."""
    for d in rows:
        ts = datetime.combine(d.day, time(12, 0), tzinfo=UTC)
        existing = (
            await session.execute(
                select(MarketingMetric).where(
                    and_(
                        MarketingMetric.account_id == account_id,
                        MarketingMetric.timestamp == ts,
                        MarketingMetric.intensity == 0,
                    )
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            session.add(
                MarketingMetric(
                    account_id=account_id, timestamp=ts,
                    spend=d.spend, revenue=d.revenue,
                    impressions=d.impressions, clicks=d.clicks,
                    orders=d.orders, acos=d.acos,
                    intensity=0,  # sentinel: this is a daily roll-up, not hourly
                )
            )
        else:
            existing.spend = d.spend
            existing.revenue = d.revenue
            existing.impressions = d.impressions
            existing.clicks = d.clicks
            existing.orders = d.orders
            existing.acos = d.acos


def _rollup_campaign_perf(
    perf: list[CampaignPerformance],
) -> dict[int, dict[str, float | int | None]]:
    """Sum per-campaign daily rows into one 7d roll-up per campaign_id.
    Shape matches what MarketingCampaign needs (spend/revenue/impressions/acos)."""
    out: dict[int, dict[str, float | int | None]] = {}
    for r in perf:
        slot = out.setdefault(
            r.campaign_id,
            {"spend": 0.0, "revenue": 0.0, "impressions": 0, "orders": 0},
        )
        slot["spend"] = float(slot["spend"]) + r.spend  # type: ignore[arg-type]
        slot["revenue"] = float(slot["revenue"]) + r.revenue  # type: ignore[arg-type]
        slot["impressions"] = int(slot["impressions"]) + r.impressions  # type: ignore[arg-type]
        slot["orders"] = int(slot["orders"]) + r.orders  # type: ignore[arg-type]
    for cid, slot in out.items():
        spend = float(slot["spend"])  # type: ignore[arg-type]
        rev = float(slot["revenue"])  # type: ignore[arg-type]
        slot["acos"] = round(spend / rev * 100, 2) if rev > 0 else None
    return out


def _map_status(shopee_status: str) -> str:
    s = (shopee_status or "").lower()
    if s in ("ongoing", "active", "running"):
        return "active"
    if s == "paused":
        return "paused"
    if s in ("ended", "expired", "deleted"):
        return "off"
    if s == "scheduled":
        return "active"
    return s or "active"


async def _upsert_campaigns(
    session: AsyncSession,
    *,
    account_id: UUID,
    shop_balance: float,
    info_list: list[CampaignInfo],
    perf_by_campaign: dict[int, dict[str, float | int | None]],
) -> None:
    """Upsert by (account_id, external_id=campaign_id). `credit` reflects
    the SHOP-level balance for every campaign (Shopee Ads doesn't expose
    per-campaign credit) so the Campanhas grid can show the available
    pool consistently."""
    existing = (
        await session.execute(
            select(MarketingCampaign).where(MarketingCampaign.account_id == account_id)
        )
    ).scalars().all()
    by_ext = {c.external_id: c for c in existing if c.external_id}

    seen_ids: set[str] = set()
    for info in info_list:
        ext_id = str(info.campaign_id)
        seen_ids.add(ext_id)
        perf = perf_by_campaign.get(info.campaign_id, {})
        spend = float(perf.get("spend") or 0)
        revenue = float(perf.get("revenue") or 0)
        impressions = int(perf.get("impressions") or 0)
        acos = perf.get("acos")
        status = _map_status(info.status)
        camp = by_ext.get(ext_id)
        if camp is None:
            session.add(
                MarketingCampaign(
                    account_id=account_id,
                    name=info.name or f"Campanha {info.campaign_id}",
                    external_id=ext_id,
                    status=status,
                    credit=shop_balance,
                    spend=spend, revenue=revenue,
                    impressions=impressions,
                    acos=float(acos) if acos is not None else None,
                )
            )
        else:
            camp.name = info.name or camp.name
            camp.status = status
            camp.credit = shop_balance
            camp.spend = spend
            camp.revenue = revenue
            camp.impressions = impressions
            camp.acos = float(acos) if acos is not None else None
