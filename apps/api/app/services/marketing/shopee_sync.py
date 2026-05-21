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

import asyncio
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.integration import Integration
from app.models.marketing import MarketingAccount, MarketingCampaign, MarketingMetric
from app.redis_client import redis
from app.security.cipher import decrypt_json, encrypt_json
from app.services.marketing.alerts import (
    notify_high_acos,
    notify_low_credit,
    record_sync_failure,
    record_sync_success,
)
from app.services.marketing.bling_revenue import BlingRevenue, get_bling_revenue
from app.services.marketplaces.shopee import ShopeeClient
from app.services.shopee_ads import (
    CampaignInfo,
    CampaignPerformance,
    DailyMetric,
    HourlyMetric,
    ShopeeAdsClient,
    ShopeeAdsError,
    ShopeeAdsRateLimit,
)
from app.services.telegram import TelegramClient

logger = structlog.get_logger()

# Redis key for the global Shopee Ads cooldown. Set after the first
# `ads_rate_limit_total_api` hit so the next cron tick skips Shopee
# entirely instead of hammering the throttle until the partner gets
# suspended. Cleared automatically by Redis TTL expiry.
_COOLDOWN_KEY = "shopee_ads:global_cooldown"

# Errors that should NOT increment Integration.consecutive_errors. These
# are operational states (no permission, no Ads enabled, rate-limited) —
# not flapping bugs — so they shouldn't fire the "3 failures in a row"
# Telegram alert. The sync just notes the state and moves on.
_EXPECTED_SHOPEE_ERRORS = frozenset({
    "error_permission_denied",
    "error_permission",
    "ads_rate_limit_total_api",
    "ads.rate_limit.exceed_api",
    "error_rate_limit_total_api",
    "rate_limit_exhausted",
})

# Balance is the only Shopee endpoint with a sub-1-call-per-minute global
# throttle. We cache it on MarketingAccount and only refresh ONE shop per
# sync_all cycle — the one with the oldest `credit_balance_at`. With 13
# shops × 30-min cron, all balances refresh within ~6.5h, comfortably
# under the 6h cache TTL.
_BALANCE_CACHE_TTL = timedelta(hours=6)

# Pacing between shops in sync_all_shopee_integrations. Generous enough
# that the per-account-call endpoints (daily, hourly, campaigns) don't
# trip Shopee's softer throttles even when we have 13 shops queued.
_INTER_SHOP_DELAY = 5.0

# Round-robin: minimum time between syncs of the SAME shop. With cron
# every 5min × 13 shops, each shop refreshes ~once per 65min — well
# above this floor.
_MIN_SHOP_REFRESH = timedelta(hours=2)

# Pacing between API calls WITHIN a single shop sync. Now read from
# `settings.shopee_ads_delay_between_calls_s` so the operator can tune
# without redeploying. Default 30s — Shopee's per-partner Ads throttle
# is so tight that 10s wasn't enough.
def _inter_call_delay() -> float:
    return float(get_settings().shopee_ads_delay_between_calls_s)


async def _is_on_cooldown() -> bool:
    """True if a previous tick set the global Shopee Ads cooldown."""
    try:
        return bool(await redis.exists(_COOLDOWN_KEY))
    except Exception:  # noqa: BLE001
        return False


async def _set_cooldown(reason: str) -> None:
    """Lock Shopee out for `settings.shopee_ads_cooldown_on_rate_limit_s`
    seconds (default 3600 = 1h). Best-effort Telegram alert so the
    operator knows the lockout fired."""
    ttl = int(get_settings().shopee_ads_cooldown_on_rate_limit_s)
    try:
        await redis.set(_COOLDOWN_KEY, reason, ex=ttl)
    except Exception as e:  # noqa: BLE001
        logger.warning("shopee_ads_cooldown_set_failed", err=str(e)[:200])
        return
    logger.warning("shopee_ads_cooldown_activated", reason=reason, ttl_seconds=ttl)
    try:
        await TelegramClient().safe_send(
            f"⚠️ Shopee Ads RATE LIMITED — cooldown global ativo por {ttl // 60} min.\n"
            f"Motivo: <code>{reason}</code>"
        )
    except Exception:  # noqa: BLE001
        pass

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

    # Composition: build a ShopeeClient (handles HMAC + token refresh)
    # and wrap it in ShopeeAdsClient. Keeps Ads surface fully isolated
    # from any future change to ShopeeClient.
    shopee = ShopeeClient(creds, on_token_refresh=_persist_refreshed_creds)
    client = ShopeeAdsClient(shopee)

    today = datetime.now(UTC).date()
    start_day = today - timedelta(days=_DAILY_LOOKBACK_DAYS - 1)

    # Lookup the MarketingAccount upfront so we know if cached balance
    # is available. The account is created/updated later, but its
    # `credit_balance` + `credit_balance_at` survive across syncs.
    existing_account = (
        await session.execute(
            select(MarketingAccount).where(
                MarketingAccount.integration_id == integration.id
            )
        )
    ).scalar_one_or_none()
    cached_balance = (existing_account.credit_balance if existing_account else None)
    cached_balance_at = (existing_account.credit_balance_at if existing_account else None)

    # Balance is fetched lazily — only if the cache is stale. This dodges
    # Shopee's ultra-tight per-endpoint throttle that otherwise fails all
    # 13 shops in a row.
    balance: float | None = cached_balance
    balance_refreshed = False
    try:
        if _should_refresh_balance(cached_balance_at):
            try:
                balance = await client.get_balance()
                balance_refreshed = True
            except ShopeeAdsRateLimit as rl:
                # Rate-limited: keep cached value (if any), don't fail.
                # ALSO arm the global cooldown so we don't hammer the
                # partner-wide throttle through other shops this cycle.
                logger.warning(
                    "shopee_ads_balance_rate_limited_using_cache",
                    integration_id=str(integration_id),
                    cached_balance=cached_balance,
                    cached_at=cached_balance_at.isoformat() if cached_balance_at else None,
                    code=rl.code,
                )
                await _set_cooldown(f"balance:{rl.code}")
                # Bail out — the rest of the shop's endpoints would just
                # add more 429s to the partner's counter.
                await session.commit()
                return {"status": "skipped", "code": rl.code, "message": "rate_limited_cooldown_set"}
            # Pace before the next call regardless of outcome — Shopee's
            # throttle counts attempted calls too.
            await asyncio.sleep(_inter_call_delay())
        daily = await client.get_daily_performance(start_day, today)
        await asyncio.sleep(_inter_call_delay())
        hourly = await client.get_hourly_performance(today) if pull_hourly else []
        if pull_hourly:
            await asyncio.sleep(_inter_call_delay())
        campaign_ids = await client.list_campaign_ids() if pull_campaigns else []
        if pull_campaigns and campaign_ids:
            await asyncio.sleep(_inter_call_delay())
        campaign_info = (
            await client.get_campaign_settings(campaign_ids) if campaign_ids else []
        )
        if campaign_info:
            await asyncio.sleep(_inter_call_delay())
        campaign_perf = (
            await client.get_campaign_performance(campaign_ids, start_day, today)
            if campaign_ids
            else []
        )
    except ShopeeAdsRateLimit as e:
        # Non-balance rate limit on a campaign/perf endpoint — arm the
        # global cooldown and bail. consecutive_errors stays at 0 since
        # this is operational, not a flapping bug.
        integration.last_error = f"{e.code}: {e.message}"[:500]
        await _set_cooldown(f"perf:{e.code}")
        await session.commit()
        return {"status": "skipped", "code": e.code, "message": e.message}
    except ShopeeAdsError as e:
        integration.last_error = f"{e.code}: {e.message}"[:500]
        # Expected operational errors (no Ads permission, etc.) don't
        # bump the failure counter — only flapping bugs do.
        if e.code not in _EXPECTED_SHOPEE_ERRORS:
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
        await session.commit()
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
        balance_just_refreshed=balance_refreshed,
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
    integration.last_ads_sync_at = datetime.now(UTC)
    await record_sync_success(session, integration)
    await session.commit()

    # ─── post-commit Telegram alerts (best-effort, deduped) ──────────────
    if balance is not None:
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
    plain stock/order sync doesn't accidentally burn ads-API quota.

    Pacing: `_INTER_SHOP_DELAY` seconds between shops. Combined with the
    lazy balance fetch (only the stalest shop hits get_total_balance per
    cycle) this keeps the whole loop comfortably under Shopee's
    per-endpoint throttles. Still slower than the cron-tick budget on
    13 shops though — for the steady-state, prefer `sync_shopee_single_next`."""
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
    for idx, integ in enumerate(rows):
        if idx > 0:
            await asyncio.sleep(_INTER_SHOP_DELAY)
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


async def sync_shopee_single_next(session: AsyncSession) -> dict[str, Any]:
    """Round-robin: pick the one Shopee integration whose
    `last_ads_sync_at` is oldest (NULL → never synced → first in line)
    and sync only that shop. Designed for a 5-minute cron — across 13
    shops, each refreshes ~once an hour, comfortably under Shopee's
    per-partner Ads throttle even when individual calls retry.

    Even if the shop sync fails (rate-limited, permission denied, etc.)
    we stamp `last_ads_sync_at` so the next tick rotates to a different
    shop instead of getting stuck on the same one.

    Skips shops whose `last_ads_sync_at` is within `_MIN_SHOP_REFRESH`
    (2h) — returns `{status: 'all_fresh'}` when every eligible shop is
    already recent.

    Honours the global Shopee Ads cooldown (Redis-backed) — if a
    previous tick got rate-limited, returns `{status: 'on_cooldown'}`
    without making any API calls."""
    if await _is_on_cooldown():
        try:
            ttl = await redis.ttl(_COOLDOWN_KEY)
        except Exception:  # noqa: BLE001
            ttl = -1
        return {
            "status": "on_cooldown",
            "cooldown_remaining_seconds": max(ttl, 0),
        }
    integ = (
        await session.execute(
            select(Integration)
            .where(
                and_(
                    Integration.status == "active",
                    Integration.platform == "shopee",
                    Integration.ads_enabled.is_(True),
                )
            )
            # NULLs FIRST so never-synced shops are processed before
            # already-synced ones. Then oldest first.
            .order_by(Integration.last_ads_sync_at.asc().nulls_first())
            .limit(1)
        )
    ).scalar_one_or_none()

    if integ is None:
        return {"status": "no_shopee_integrations"}

    # Honour the cooldown so we don't repeatedly hammer the same shop
    # if it's the only ads_enabled one or the others are mid-flight.
    if integ.last_ads_sync_at is not None:
        age = datetime.now(UTC) - integ.last_ads_sync_at
        if age < _MIN_SHOP_REFRESH:
            return {
                "status": "all_fresh",
                "integration_id": str(integ.id),
                "age_minutes": int(age.total_seconds() / 60),
            }

    integration_id = integ.id
    name = integ.name
    try:
        result = await sync_shopee_integration(session, integration_id)
    except Exception as e:  # noqa: BLE001
        logger.error(
            "shopee_ads_single_sync_failed",
            integration_id=str(integration_id), err=str(e)[:300],
        )
        result = {"status": "error", "error": str(e)[:200]}

    # Always advance the cursor — even on failure — so the next tick
    # picks a DIFFERENT shop instead of getting stuck on this one.
    # sync_shopee_integration already stamps last_ads_sync_at on success;
    # on failure we stamp here to release the round-robin.
    integ_refreshed = await session.get(Integration, integration_id)
    if integ_refreshed and integ_refreshed.last_ads_sync_at is None:
        integ_refreshed.last_ads_sync_at = datetime.now(UTC)
        await session.commit()

    return {"integration_id": str(integration_id), "name": name, **result}


# ─── internals ────────────────────────────────────────────────────────────


def _should_refresh_balance(last_at: datetime | None) -> bool:
    """True if cached balance is stale (or never set). 6h TTL chosen so
    that across 13 shops × 30-min cron, every shop refreshes well within
    the window without any one tick trying multiple balances back-to-back."""
    if last_at is None:
        return True
    age = datetime.now(UTC) - last_at
    return age >= _BALANCE_CACHE_TTL


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
    balance: float | None,
    daily: list[DailyMetric],
    today: date,
    balance_just_refreshed: bool = False,
) -> MarketingAccount:
    """Resolve the (integration_id) MarketingAccount or create one. Each
    Shopee integration owns exactly one MarketingAccount (1 loja = 1
    dept, per the spec); the department comes from `Integration.department`
    when set, else falls back to 'geral'. The mock seed creates
    dept-specific Shopee rows with integration_id=NULL — those stay
    untouched here so /seed and live data can coexist.

    `balance_just_refreshed` is True only when this sync actually hit
    `get_total_balance` and got a fresh number. In that case we stamp
    `credit_balance_at` with now() so the next sync respects the 6h TTL.
    When False, `balance` was read from the cache — don't move the
    timestamp."""
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
            credit_balance_at=datetime.now(UTC) if balance_just_refreshed else None,
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
        if balance is not None:
            existing.credit_balance = balance
        if balance_just_refreshed:
            existing.credit_balance_at = datetime.now(UTC)
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
