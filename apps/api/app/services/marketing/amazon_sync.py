"""Pull live Amazon Sponsored Products numbers into the marketing_* tables.

Differences from Shopee/ML:

  • Amazon Ads creds are global env vars (one Advertising profile per
    marketplace), not per-Integration. The orchestrator still iterates
    Integration rows so each Amazon shop gets its own MarketingAccount,
    but every sync uses the same client.
  • No credit pot — `credit_balance` = remaining daily budget today.
  • Reports are async; we already wait inside the client. Each Integration
    triggers its own report (could be optimized later by sharing one
    report across all Amazon integrations on the same profile, but the
    1-per-shop pattern is simpler and below Amazon's rate limits).
  • There's no native per-day breakdown without per-day TimeUnit reports.
    For now we emit one MarketingMetric row at the window end (mirrors
    ML), and the orchestrator joins Bling per-day Bling revenue separately.
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
from app.services.amazon_ads import (
    AmazonAdsClient,
    AmazonAdsError,
    AmazonAdsMissingCreds,
    AmazonCampaign,
)
from app.services.marketing.alerts import (
    notify_high_acos,
    record_sync_failure,
    record_sync_success,
)
from app.services.marketing.bling_revenue import get_bling_revenue

logger = structlog.get_logger()

_DAILY_LOOKBACK_DAYS = 7


async def sync_amazon_integration(
    session: AsyncSession,
    integration_id: UUID,
) -> dict[str, Any]:
    integration = await session.get(Integration, integration_id)
    if integration is None:
        return {"status": "error", "code": "integration_not_found"}
    if integration.platform.value != "amazon":
        return {"status": "skipped", "reason": "not_amazon", "platform": integration.platform.value}

    try:
        client = AmazonAdsClient()
    except AmazonAdsMissingCreds:
        return {
            "status": "skipped",
            "code": "missing_credentials",
            "message": "AMAZON_ADS_* env vars not set",
        }

    today = datetime.now(UTC).date()
    start_day = today - timedelta(days=_DAILY_LOOKBACK_DAYS - 1)

    try:
        campaigns = await client.get_campaign_performance(start_day, today)
        remaining_budget = await client.get_remaining_daily_budget(
            [{"campaignId": c.campaign_id, "state": c.state.upper(),
              "budget": {"budget": c.daily_budget}} for c in campaigns]
        )
    except AmazonAdsError as e:
        integration.last_error = f"{e.code}: {e.message}"[:500]
        await record_sync_failure(session, integration, code=e.code, message=e.message)
        await session.commit()
        if e.code == "report_timeout":
            logger.warning("amazon_ads_report_timeout", integration_id=str(integration_id))
            return {"status": "skipped", "code": "report_timeout"}
        raise

    spend = sum(c.spend for c in campaigns)
    impressions = sum(c.impressions for c in campaigns)
    clicks = sum(c.clicks for c in campaigns)
    amazon_sales = sum(c.sales_14d for c in campaigns)  # fallback when Bling absent

    # Bling authoritative revenue
    bling = await get_bling_revenue(session, integration, start=start_day, end=today)
    # Pega APENAS o faturamento de HOJE (não o somatório da janela). Veja
    # ml_sync.py — mesmo bug histórico inflava agregados 7d/30d.
    bling_today = bling.by_day.get(today, 0.0) if bling else 0.0
    account_revenue = bling_today if bling else amazon_sales
    account_acos = (
        round(spend / account_revenue * 100, 2) if account_revenue > 0 else None
    )

    account = await _upsert_account(
        session,
        integration=integration,
        credit=remaining_budget,
        spend=spend,
        revenue=account_revenue,
        impressions=impressions,
    )

    await _upsert_daily_metric(
        session, account_id=account.id, day=today,
        spend=spend, revenue=account_revenue,
        impressions=impressions, clicks=clicks,
        acos=account_acos,
    )

    await _upsert_campaigns(session, account_id=account.id, campaigns=campaigns)

    integration.last_error = None
    integration.last_test_ok = True
    integration.last_test_at = datetime.now(UTC)
    await record_sync_success(session, integration)
    await session.commit()

    if account_acos is not None:
        await notify_high_acos(integration, account_acos, account.acos_target)

    return {
        "status": "ok",
        "account_id": str(account.id),
        "credit": remaining_budget,
        "spend": spend,
        "revenue": account_revenue,
        "campaigns": len(campaigns),
        "bling_revenue": bling.total if bling else None,
    }


async def sync_all_amazon_integrations(session: AsyncSession) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(Integration).where(
                and_(
                    Integration.status == "active",
                    Integration.platform == "amazon",
                    Integration.ads_enabled.is_(True),
                )
            )
        )
    ).scalars().all()
    out: list[dict[str, Any]] = []
    for integ in rows:
        try:
            r = await sync_amazon_integration(session, integ.id)
            out.append({"integration_id": str(integ.id), **r})
        except Exception as e:  # noqa: BLE001
            logger.error("amazon_ads_sync_failed", integration_id=str(integ.id), err=str(e)[:300])
            out.append({"integration_id": str(integ.id), "status": "error", "error": str(e)[:200]})
    return out


# ─── internals (same shape as ml_sync) ───────────────────────────────────


async def _upsert_account(
    session: AsyncSession, *,
    integration: Integration, credit: float, spend: float,
    revenue: float, impressions: int,
) -> MarketingAccount:
    dept = (integration.department or "geral").lower()
    existing = (
        await session.execute(
            select(MarketingAccount).where(
                MarketingAccount.integration_id == integration.id
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        existing = MarketingAccount(
            integration_id=integration.id, name=integration.name,
            platform="amazon", department=dept,
            acos_target=8.0, credit_balance=credit,
            agent_enabled=False, status="active",
            spend_today=spend, revenue_today=revenue,
            impressions_today=impressions,
        )
        session.add(existing)
        await session.flush()
    else:
        existing.platform = "amazon"
        existing.department = dept
        existing.credit_balance = credit
        existing.spend_today = spend
        existing.revenue_today = revenue
        existing.impressions_today = impressions
        existing.status = "active"
    return existing


async def _upsert_daily_metric(
    session: AsyncSession, *,
    account_id: UUID, day: date,
    spend: float, revenue: float, impressions: int, clicks: int,
    acos: float | None,
) -> None:
    ts = datetime.combine(day, time(12, 0), tzinfo=UTC)
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
        session.add(MarketingMetric(
            account_id=account_id, timestamp=ts,
            spend=spend, revenue=revenue,
            impressions=impressions, clicks=clicks,
            orders=0, acos=acos, intensity=0,
        ))
    else:
        existing.spend = spend
        existing.revenue = revenue
        existing.impressions = impressions
        existing.clicks = clicks
        existing.acos = acos


def _map_amazon_state(state: str) -> str:
    s = (state or "").lower()
    if s == "enabled":
        return "active"
    if s == "paused":
        return "paused"
    if s in ("archived", "ended"):
        return "off"
    return s or "active"


async def _upsert_campaigns(
    session: AsyncSession, *,
    account_id: UUID, campaigns: list[AmazonCampaign],
) -> None:
    existing = (
        await session.execute(
            select(MarketingCampaign).where(MarketingCampaign.account_id == account_id)
        )
    ).scalars().all()
    by_ext = {c.external_id: c for c in existing if c.external_id}
    for camp in campaigns:
        ext_id = str(camp.campaign_id)
        status = _map_amazon_state(camp.state)
        row = by_ext.get(ext_id)
        if row is None:
            session.add(MarketingCampaign(
                account_id=account_id,
                name=camp.name or f"Campanha {camp.campaign_id}",
                external_id=ext_id, status=status,
                credit=None,
                spend=camp.spend, revenue=camp.sales_14d,
                impressions=camp.impressions, acos=camp.acos,
            ))
        else:
            row.name = camp.name or row.name
            row.status = status
            row.spend = camp.spend
            row.revenue = camp.sales_14d
            row.impressions = camp.impressions
            row.acos = camp.acos
