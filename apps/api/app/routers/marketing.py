"""Marketing module HTTP API.

Endpoints (all under /api/marketing):
  accounts, metrics (summary + per-account), schedules CRUD,
  agents/status, decisions, intensity, patterns, chart-data, seed, trigger.

Auth: require_active_user. The marketing data is not user-scoped (it's a
single-tenant ops dashboard for the whole shop).
"""
from __future__ import annotations

import random
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
import sqlalchemy as sa
from sqlalchemy import and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_active_user
from app.models import User
from app.models.marketing import (
    MarketingAccount,
    MarketingCampaign,
    MarketingDecision,
    MarketingMetric,
    MarketingPattern,
    MarketingSchedule,
)
from app.services.marketing.agent import (
    agent_decision_cycle,
    calculate_intensity,
    collect_signals,
)

logger = structlog.get_logger()
router = APIRouter(prefix="/api/marketing", tags=["marketing"])


# ─── schemas ──────────────────────────────────────────────────────────


class AccountOut(BaseModel):
    id: UUID
    name: str
    platform: str
    department: str
    acos_target: float
    daily_budget: float | None
    agent_enabled: bool
    status: str
    current_intensity: int
    credit_balance: float | None
    spend_today: float
    revenue_today: float
    impressions_today: int


class ScheduleIn(BaseModel):
    day_of_week: int = Field(ge=0, le=6)
    start_hour: int = Field(ge=0, le=23)
    end_hour: int = Field(ge=0, le=23)


class ScheduleOut(BaseModel):
    id: UUID
    account_id: UUID
    day_of_week: int
    start_hour: int
    end_hour: int


class CopyScheduleIn(BaseModel):
    target_account_id: UUID


class DecisionOut(BaseModel):
    id: UUID
    account_id: UUID
    account_name: str
    platform: str
    department: str
    timestamp: str
    action: str
    reasoning: str
    market_intensity: int
    in_base_window: bool
    acos_at_time: float | None


class PatternOut(BaseModel):
    id: UUID
    account_id: UUID
    pattern_type: str
    description: str
    confidence: float
    active: bool
    discovered_at: str


class IntensityOut(BaseModel):
    account_id: UUID
    name: str
    platform: str
    department: str
    intensity: int
    assessment: str
    status: str


class AgentStatusOut(BaseModel):
    platform: str
    running: bool
    last_decision_at: str | None
    last_decision_minutes_ago: int | None
    decisions_today: int


class CreditAlertOut(BaseModel):
    account_id: UUID
    name: str
    department: str
    credit_balance: float
    daily_spend_avg: float
    days_remaining: float
    severity: str  # "ok" | "warning" | "critical"


# ─── helpers ──────────────────────────────────────────────────────────


def _account_out(a: MarketingAccount) -> AccountOut:
    return AccountOut(
        id=a.id, name=a.name, platform=a.platform, department=a.department,
        acos_target=a.acos_target, daily_budget=a.daily_budget,
        agent_enabled=a.agent_enabled, status=a.status,
        current_intensity=a.current_intensity, credit_balance=a.credit_balance,
        spend_today=a.spend_today, revenue_today=a.revenue_today,
        impressions_today=a.impressions_today,
    )


def _assessment(intensity: int) -> str:
    if intensity > 80:
        return "pico"
    if intensity > 60:
        return "aquecido"
    if intensity > 30:
        return "normal"
    if intensity > 15:
        return "fraco"
    return "morto"


# ─── ACCOUNTS / METRICS ───────────────────────────────────────────────


@router.get("/accounts")
async def list_accounts(
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_active_user)],
    department: str | None = Query(None),
    platform: str | None = Query(None),
) -> list[AccountOut]:
    stmt = select(MarketingAccount)
    if department:
        stmt = stmt.where(MarketingAccount.department == department)
    if platform:
        stmt = stmt.where(MarketingAccount.platform == platform)
    rows = (
        await session.execute(stmt.order_by(MarketingAccount.platform, MarketingAccount.name))
    ).scalars().all()
    return [_account_out(a) for a in rows]


@router.get("/metrics/summary")
async def metrics_summary(
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_active_user)],
    department: str | None = Query(None),
    platform: str | None = Query(None),
    period1_days: int = Query(7, ge=1, le=365),
    period2_days: int = Query(30, ge=1, le=365),
) -> dict:
    stmt = select(MarketingAccount)
    if department:
        stmt = stmt.where(MarketingAccount.department == department)
    if platform:
        stmt = stmt.where(MarketingAccount.platform == platform)
    accounts = (
        await session.execute(stmt.order_by(MarketingAccount.platform, MarketingAccount.name))
    ).scalars().all()

    now = datetime.now(UTC)

    async def aggregate(days: int) -> dict[str, dict]:
        since = now - timedelta(days=days)
        out: dict[str, dict] = {}
        for acc in accounts:
            row = (
                await session.execute(
                    select(
                        func.coalesce(func.sum(MarketingMetric.spend), 0.0).label("spend"),
                        func.coalesce(func.sum(MarketingMetric.revenue), 0.0).label("revenue"),
                        func.coalesce(func.sum(MarketingMetric.impressions), 0).label("impressions"),
                        func.coalesce(func.sum(MarketingMetric.clicks), 0).label("clicks"),
                        func.coalesce(func.sum(MarketingMetric.orders), 0).label("orders"),
                        func.avg(MarketingMetric.acos).label("acos"),
                    ).where(
                        and_(
                            MarketingMetric.account_id == acc.id,
                            MarketingMetric.timestamp >= since,
                        )
                    )
                )
            ).first()
            spend = float(row.spend or 0)
            revenue = float(row.revenue or 0)
            out[str(acc.id)] = {
                "credit": acc.credit_balance if acc.platform == "shopee" else None,
                "spend": round(spend, 2),
                "revenue": round(revenue, 2),
                "impressions": int(row.impressions or 0),
                "clicks": int(row.clicks or 0),
                "orders": int(row.orders or 0),
                "acos": round(float(row.acos), 1) if row.acos is not None else None,
                "status": acc.status,
            }
        return out

    return {
        "accounts": [_account_out(a) for a in accounts],
        "period_1": {"days": period1_days, "data": await aggregate(period1_days)},
        "period_2": {"days": period2_days, "data": await aggregate(period2_days)},
    }


@router.get("/metrics/{account_id}")
async def metrics_by_account(
    account_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_active_user)],
    start: date | None = Query(None),
    end: date | None = Query(None),
) -> list[dict]:
    now = datetime.now(UTC)
    start_dt = (
        datetime.combine(start, time.min, tzinfo=UTC)
        if start else now - timedelta(days=7)
    )
    end_dt = (
        datetime.combine(end, time.max, tzinfo=UTC) if end else now
    )
    rows = (
        await session.execute(
            select(MarketingMetric)
            .where(
                and_(
                    MarketingMetric.account_id == account_id,
                    MarketingMetric.timestamp >= start_dt,
                    MarketingMetric.timestamp <= end_dt,
                )
            )
            .order_by(MarketingMetric.timestamp.asc())
        )
    ).scalars().all()
    return [
        {
            "timestamp": m.timestamp.isoformat(),
            "spend": float(m.spend), "revenue": float(m.revenue),
            "impressions": m.impressions, "clicks": m.clicks, "orders": m.orders,
            "acos": float(m.acos) if m.acos is not None else None,
            "intensity": m.intensity,
        }
        for m in rows
    ]


# ─── SCHEDULES ────────────────────────────────────────────────────────


@router.get("/schedules/{account_id}")
async def get_schedules(
    account_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_active_user)],
) -> list[ScheduleOut]:
    rows = (
        await session.execute(
            select(MarketingSchedule)
            .where(MarketingSchedule.account_id == account_id)
            .order_by(MarketingSchedule.day_of_week, MarketingSchedule.start_hour)
        )
    ).scalars().all()
    return [
        ScheduleOut(
            id=s.id, account_id=s.account_id,
            day_of_week=s.day_of_week, start_hour=s.start_hour, end_hour=s.end_hour,
        )
        for s in rows
    ]


@router.put("/schedules/{account_id}")
async def replace_schedules(
    account_id: UUID,
    schedules: list[ScheduleIn],
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_active_user)],
) -> list[ScheduleOut]:
    acc = await session.get(MarketingAccount, account_id)
    if acc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "account_not_found"})
    await session.execute(
        delete(MarketingSchedule).where(MarketingSchedule.account_id == account_id)
    )
    rows: list[MarketingSchedule] = []
    for s in schedules:
        row = MarketingSchedule(
            account_id=account_id, day_of_week=s.day_of_week,
            start_hour=s.start_hour, end_hour=s.end_hour,
        )
        session.add(row)
        rows.append(row)
    await session.commit()
    for r in rows:
        await session.refresh(r)
    return [
        ScheduleOut(
            id=r.id, account_id=r.account_id,
            day_of_week=r.day_of_week, start_hour=r.start_hour, end_hour=r.end_hour,
        )
        for r in rows
    ]


@router.get("/schedules/{account_id}/heatmap")
async def schedule_heatmap(
    account_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_active_user)],
    days: int = Query(30, ge=1, le=180),
) -> dict:
    """7×24 ACOS heatmap. Aggregates the last N days of MarketingMetric by
    (weekday, hour). Returns {acos_target, cells: {"<dow>-<hour>": {...}}}
    so the schedule UI can colour each cell by ACOS without re-aggregating
    on the client. Cells with no metrics in the window are simply absent."""
    acc = await session.get(MarketingAccount, account_id)
    if acc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "account_not_found"})
    since = datetime.now(UTC) - timedelta(days=days)
    rows = (
        await session.execute(
            select(
                func.extract("dow", MarketingMetric.timestamp).label("pg_dow"),
                func.extract("hour", MarketingMetric.timestamp).label("hour"),
                func.coalesce(func.sum(MarketingMetric.spend), 0.0).label("spend"),
                func.coalesce(func.sum(MarketingMetric.revenue), 0.0).label("revenue"),
                func.coalesce(func.sum(MarketingMetric.impressions), 0).label("impressions"),
            )
            .where(
                and_(
                    MarketingMetric.account_id == account_id,
                    MarketingMetric.timestamp >= since,
                )
            )
            .group_by("pg_dow", "hour")
        )
    ).all()
    # Postgres EXTRACT(dow): 0=Sun..6=Sat. Convert to py weekday (0=Mon..6=Sun)
    # so it matches the UI's days = ['Seg','Ter','Qua','Qui','Sex','Sáb','Dom'].
    cells: dict[str, dict] = {}
    for r in rows:
        py_dow = (int(r.pg_dow) - 1) % 7
        spend = float(r.spend or 0)
        revenue = float(r.revenue or 0)
        acos = round(spend / revenue * 100, 1) if revenue > 0 else None
        cells[f"{py_dow}-{int(r.hour)}"] = {
            "spend": round(spend, 2),
            "revenue": round(revenue, 2),
            "impressions": int(r.impressions or 0),
            "acos": acos,
        }
    return {"acos_target": acc.acos_target, "cells": cells}


@router.post("/schedules/{account_id}/copy")
async def copy_schedules(
    account_id: UUID,
    body: CopyScheduleIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_active_user)],
) -> list[ScheduleOut]:
    src = await session.get(MarketingAccount, account_id)
    tgt = await session.get(MarketingAccount, body.target_account_id)
    if src is None or tgt is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "account_not_found"})
    src_rows = (
        await session.execute(
            select(MarketingSchedule).where(MarketingSchedule.account_id == src.id)
        )
    ).scalars().all()
    await session.execute(
        delete(MarketingSchedule).where(MarketingSchedule.account_id == tgt.id)
    )
    copied: list[MarketingSchedule] = []
    for s in src_rows:
        row = MarketingSchedule(
            account_id=tgt.id, day_of_week=s.day_of_week,
            start_hour=s.start_hour, end_hour=s.end_hour,
        )
        session.add(row)
        copied.append(row)
    await session.commit()
    for r in copied:
        await session.refresh(r)
    return [
        ScheduleOut(
            id=r.id, account_id=r.account_id,
            day_of_week=r.day_of_week, start_hour=r.start_hour, end_hour=r.end_hour,
        )
        for r in copied
    ]


# ─── AGENTS ───────────────────────────────────────────────────────────


@router.get("/agents/status")
async def agents_status(
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_active_user)],
) -> list[AgentStatusOut]:
    now = datetime.now(UTC)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    out: list[AgentStatusOut] = []
    for platform in ("shopee", "ml", "amazon"):
        last = (
            await session.execute(
                select(MarketingDecision)
                .join(
                    MarketingAccount,
                    MarketingAccount.id == MarketingDecision.account_id,
                )
                .where(MarketingAccount.platform == platform)
                .order_by(MarketingDecision.timestamp.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        n_today = (
            await session.execute(
                select(func.count())
                .select_from(MarketingDecision)
                .join(
                    MarketingAccount,
                    MarketingAccount.id == MarketingDecision.account_id,
                )
                .where(
                    and_(
                        MarketingAccount.platform == platform,
                        MarketingDecision.timestamp >= today_start,
                    )
                )
            )
        ).scalar_one()
        out.append(
            AgentStatusOut(
                platform=platform,
                running=True,
                last_decision_at=last.timestamp.isoformat() if last else None,
                last_decision_minutes_ago=(
                    int((now - last.timestamp).total_seconds() / 60) if last else None
                ),
                decisions_today=int(n_today),
            )
        )
    return out


@router.get("/decisions")
async def list_decisions(
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_active_user)],
    account_id: UUID | None = Query(None),
    platform: str | None = Query(None),
    department: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
) -> list[DecisionOut]:
    stmt = (
        select(MarketingDecision, MarketingAccount)
        .join(MarketingAccount, MarketingAccount.id == MarketingDecision.account_id)
    )
    if account_id:
        stmt = stmt.where(MarketingDecision.account_id == account_id)
    if platform:
        stmt = stmt.where(MarketingAccount.platform == platform)
    if department:
        stmt = stmt.where(MarketingAccount.department == department)
    stmt = stmt.order_by(MarketingDecision.timestamp.desc()).limit(limit)
    rows = (await session.execute(stmt)).all()
    return [
        DecisionOut(
            id=d.id, account_id=d.account_id, account_name=acc.name,
            platform=acc.platform, department=acc.department,
            timestamp=d.timestamp.isoformat(), action=d.action,
            reasoning=d.reasoning, market_intensity=d.market_intensity,
            in_base_window=d.in_base_window,
            acos_at_time=float(d.acos_at_time) if d.acos_at_time is not None else None,
        )
        for d, acc in rows
    ]


@router.get("/intensity")
async def get_intensity(
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_active_user)],
    department: str | None = Query(None),
) -> list[IntensityOut]:
    stmt = select(MarketingAccount)
    if department:
        stmt = stmt.where(MarketingAccount.department == department)
    rows = (
        await session.execute(stmt.order_by(MarketingAccount.platform, MarketingAccount.name))
    ).scalars().all()
    out: list[IntensityOut] = []
    for acc in rows:
        signals = await collect_signals(acc)
        intensity = calculate_intensity(
            signals["sales_last_hour"], signals["avg_sales_this_hour"],
            signals["impressions_last_hour"], signals["avg_impressions_this_hour"],
        )
        out.append(
            IntensityOut(
                account_id=acc.id, name=acc.name, platform=acc.platform,
                department=acc.department, intensity=intensity,
                assessment=_assessment(intensity), status=acc.status,
            )
        )
    return out


@router.get("/patterns")
async def list_patterns(
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_active_user)],
    account_id: UUID | None = Query(None),
    active: bool | None = Query(True),
) -> list[PatternOut]:
    stmt = select(MarketingPattern)
    if account_id:
        stmt = stmt.where(MarketingPattern.account_id == account_id)
    if active is not None:
        stmt = stmt.where(MarketingPattern.active.is_(active))
    rows = (
        await session.execute(stmt.order_by(MarketingPattern.confidence.desc()))
    ).scalars().all()
    return [
        PatternOut(
            id=p.id, account_id=p.account_id,
            pattern_type=p.pattern_type, description=p.description,
            confidence=p.confidence, active=p.active,
            discovered_at=p.discovered_at.isoformat(),
        )
        for p in rows
    ]


@router.get("/credit-alerts")
async def list_credit_alerts(
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_active_user)],
) -> list[CreditAlertOut]:
    """Per Shopee account: credit / avg-daily-spend → days_remaining. Severity:
    critical ≤2, warning ≤4, ok otherwise."""
    rows = (
        await session.execute(
            select(MarketingAccount).where(MarketingAccount.platform == "shopee")
        )
    ).scalars().all()
    now = datetime.now(UTC)
    week_ago = now - timedelta(days=7)
    out: list[CreditAlertOut] = []
    for acc in rows:
        if acc.credit_balance is None:
            continue
        avg = (
            await session.execute(
                select(func.coalesce(func.sum(MarketingMetric.spend), 0.0)).where(
                    and_(
                        MarketingMetric.account_id == acc.id,
                        MarketingMetric.timestamp >= week_ago,
                    )
                )
            )
        ).scalar() or 0
        daily_avg = float(avg) / 7 if avg else 0.0
        if daily_avg <= 0:
            continue
        days = acc.credit_balance / daily_avg
        severity = "critical" if days <= 2 else "warning" if days <= 4 else "ok"
        out.append(
            CreditAlertOut(
                account_id=acc.id, name=acc.name, department=acc.department,
                credit_balance=float(acc.credit_balance), daily_spend_avg=round(daily_avg, 2),
                days_remaining=round(days, 1), severity=severity,
            )
        )
    return out


# Explicit platform ordering for the spreadsheet header — Amazon → ML →
# Shopee. Mirrors how the pricing grid groups columns. Anything outside the
# trio falls back to 9 so it lands last.
_PLATFORM_ORDER = sa.case(
    (MarketingAccount.platform == "amazon", 0),
    (MarketingAccount.platform == "ml", 1),
    (MarketingAccount.platform == "shopee", 2),
    else_=9,
)


@router.get("/campaigns")
async def list_campaigns(
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_active_user)],
    department: str | None = Query(None),
    platform: str | None = Query(None),
    account_id: UUID | None = Query(None),
) -> list[dict]:
    """List campaigns joined to their account so the grid can build per-column
    headers (Name / Platform / Account / DEPARTMENT). Filter by any of the
    selectors the UI exposes. Columns are ordered Amazon → ML → Shopee,
    then by account name, then department, then campaign name."""
    stmt = select(MarketingCampaign, MarketingAccount).join(
        MarketingAccount, MarketingAccount.id == MarketingCampaign.account_id
    )
    if account_id is not None:
        stmt = stmt.where(MarketingCampaign.account_id == account_id)
    if department:
        stmt = stmt.where(MarketingAccount.department == department)
    if platform:
        stmt = stmt.where(MarketingAccount.platform == platform)
    stmt = stmt.order_by(
        _PLATFORM_ORDER,
        MarketingAccount.name,
        MarketingAccount.department,
        MarketingCampaign.name,
    )
    rows = (await session.execute(stmt)).all()
    return [_campaign_out(c, a) for c, a in rows]


@router.get("/campaigns/{campaign_id}")
async def get_campaign(
    campaign_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_active_user)],
) -> dict:
    row = (
        await session.execute(
            select(MarketingCampaign, MarketingAccount)
            .join(MarketingAccount, MarketingAccount.id == MarketingCampaign.account_id)
            .where(MarketingCampaign.id == campaign_id)
        )
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "campaign_not_found"})
    camp, acc = row
    return _campaign_out(camp, acc)


def _campaign_out(camp: MarketingCampaign, acc: MarketingAccount) -> dict:
    return {
        "id": str(camp.id),
        "account_id": str(camp.account_id),
        "account_name": acc.name,
        "platform": acc.platform,
        "department": acc.department,
        "name": camp.name,
        "external_id": camp.external_id,
        "status": camp.status,
        "credit": float(camp.credit) if camp.credit is not None else None,
        "spend": round(float(camp.spend), 2),
        "revenue": round(float(camp.revenue), 2),
        "impressions": camp.impressions,
        "acos": round(float(camp.acos), 1) if camp.acos is not None else None,
        "acos_target": acc.acos_target,
    }


@router.post("/sync-shopee/{integration_id}")
async def sync_shopee(
    integration_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_active_user)],
) -> dict:
    """Manually pull live Shopee Ads data for one integration. Persists to
    MarketingAccount + MarketingMetric + MarketingCampaign so the dashboard
    reads from DB right after the call returns."""
    from app.services.marketing.shopee_sync import sync_shopee_integration

    return await sync_shopee_integration(session, integration_id)


@router.post("/sync-shopee-all")
async def sync_shopee_all(
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_active_user)],
) -> list[dict]:
    """Loop over every active Shopee integration and sync each one. Each
    failure is recorded inline so a single bad shop doesn't abort the run."""
    from app.services.marketing.shopee_sync import sync_all_shopee_integrations

    return await sync_all_shopee_integrations(session)


@router.post("/sync-ml/{integration_id}")
async def sync_ml(
    integration_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_active_user)],
) -> dict:
    """Manually pull live ML Product Ads data for one integration."""
    from app.services.marketing.ml_sync import sync_ml_integration

    return await sync_ml_integration(session, integration_id)


@router.post("/sync-ml-all")
async def sync_ml_all(
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_active_user)],
) -> list[dict]:
    """Loop over every ads-enabled ML integration."""
    from app.services.marketing.ml_sync import sync_all_ml_integrations

    return await sync_all_ml_integrations(session)


@router.post("/sync-amazon/{integration_id}")
async def sync_amazon(
    integration_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_active_user)],
) -> dict:
    """Manually pull live Amazon Sponsored Products data for one integration."""
    from app.services.marketing.amazon_sync import sync_amazon_integration

    return await sync_amazon_integration(session, integration_id)


@router.post("/sync-amazon-all")
async def sync_amazon_all(
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_active_user)],
) -> list[dict]:
    """Loop over every ads-enabled Amazon integration."""
    from app.services.marketing.amazon_sync import sync_all_amazon_integrations

    return await sync_all_amazon_integrations(session)


@router.post("/sync-all")
async def sync_all_platforms(
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_active_user)],
) -> dict:
    """Sync every ads-enabled integration across Shopee + ML + Amazon. Each
    platform runs sequentially so a slow Amazon report doesn't block Shopee;
    each platform's failure is contained in its own block of the response."""
    from app.services.marketing.amazon_sync import sync_all_amazon_integrations
    from app.services.marketing.ml_sync import sync_all_ml_integrations
    from app.services.marketing.shopee_sync import sync_all_shopee_integrations

    shopee_results = await sync_all_shopee_integrations(session)
    ml_results = await sync_all_ml_integrations(session)
    amazon_results = await sync_all_amazon_integrations(session)
    return {
        "shopee": shopee_results,
        "mercadolivre": ml_results,
        "amazon": amazon_results,
    }


@router.post("/trigger-cycle/{account_id}")
async def trigger_cycle(
    account_id: UUID,
    _user: Annotated[User, Depends(require_active_user)],
) -> dict:
    result = await agent_decision_cycle(account_id)
    return result or {"status": "skipped", "reason": "agent_disabled"}


@router.post("/trigger-all")
async def trigger_all(
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_active_user)],
) -> list[dict]:
    rows = (
        await session.execute(
            select(MarketingAccount).where(MarketingAccount.agent_enabled.is_(True))
        )
    ).scalars().all()
    out: list[dict] = []
    for acc in rows:
        r = await agent_decision_cycle(acc.id)
        if r:
            out.append(r)
    return out


# ─── SEED ──────────────────────────────────────────────────────────────


_MOCK_ACCOUNTS = [
    ("Poofy", "shopee", "celular", 6.0, 150, 342.50),
    ("Minas", "shopee", "celular", 7.0, 100, 128.30),
    ("Poofy", "shopee", "mala", 5.0, 200, 342.50),
    ("Minas", "shopee", "mala", 6.0, 120, 128.30),
    ("Poofy", "shopee", "eletro", 6.5, 100, 342.50),
    ("Inova", "ml", "celular", 7.0, 180, None),
    ("Nexus", "ml", "mala", 6.0, 150, None),
    ("Inova", "ml", "eletro", 7.5, 130, None),
    ("Kfa", "amazon", "celular", 8.0, 120, None),
    ("Kfa", "amazon", "mala", 7.0, 100, None),
]

_MOCK_PATTERNS = [
    ("peak_hour", "Sexta 19h-23h: vendas 60% acima da média", 0.95, {"dow": 4, "hour": 20}),
    ("slow_hour", "Segunda 8h-11h: ACOS 3x maior que a média", 0.87, {"dow": 0, "hour": 9}),
    ("peak_day", "Sábado: melhor dia da semana para Shopee", 0.82, {"dow": 5}),
    ("correlation", "Quando Shopee aquece, ML aquece 2h depois", 0.68, {}),
]

# (account_name, platform, department, campaign_name, status)
_MOCK_CAMPAIGNS = [
    # Kfa — Amazon — celular
    ("Kfa", "amazon", "celular", "SP - iPhone 15 Case", "active"),
    ("Kfa", "amazon", "celular", "SP - Screen Protector", "active"),
    ("Kfa", "amazon", "celular", "SP - Fast Charger", "paused"),
    # Kfa — Amazon — mala
    ("Kfa", "amazon", "mala", "SP - Mala Viagem G", "active"),
    ("Kfa", "amazon", "mala", "SP - Mochila Executiva", "active"),
    # Inova — ML — celular
    ("Inova", "ml", "celular", "Product Ads - Celulares", "active"),
    ("Inova", "ml", "celular", "Product Ads - Acessórios", "active"),
    # Inova — ML — eletro
    ("Inova", "ml", "eletro", "Product Ads - Eletrônicos", "active"),
    ("Inova", "ml", "eletro", "Product Ads - Informática", "reduced"),
    # Nexus — ML — mala
    ("Nexus", "ml", "mala", "Product Ads - Malas", "active"),
    ("Nexus", "ml", "mala", "Product Ads - Mochilas", "active"),
    # Minas — Shopee — celular
    ("Minas", "shopee", "celular", "Fone Bluetooth TWS", "active"),
    ("Minas", "shopee", "celular", "Smartwatch Fitness", "active"),
    # Poofy — Shopee — celular
    ("Poofy", "shopee", "celular", "Capinha Silicone Premium", "active"),
    ("Poofy", "shopee", "celular", "Película Vidro 3D", "active"),
    ("Poofy", "shopee", "celular", "Carregador Turbo 65W", "reduced"),
    # Poofy — Shopee — mala
    ("Poofy", "shopee", "mala", "Mala Bordo Rígida", "active"),
    ("Poofy", "shopee", "mala", "Mochila Notebook 15pol", "active"),
    # Minas — Shopee — mala
    ("Minas", "shopee", "mala", "Necessaire Viagem", "active"),
]


def _mock_campaign_metrics(platform: str, status: str) -> dict:
    """Mock 7d metrics matching the spec ranges:
      - active: spend R$100–600, revenue 10–20x spend (ACOS 5–10%),
        impressions 3k–15k.
      - reduced: same model but at ~55% scale.
      - paused/off: all zero.
      - credit only on Shopee (R$100–400); null otherwise."""
    if status in ("paused", "off"):
        return {
            "spend": 0.0, "revenue": 0.0, "impressions": 0, "acos": None,
            "credit": round(random.uniform(100, 400), 2) if platform == "shopee" else None,
        }
    spend = round(random.uniform(100, 600), 2)
    if status == "reduced":
        spend = round(spend * 0.55, 2)
    rev_multiplier = random.uniform(10, 20)
    revenue = round(spend * rev_multiplier, 2)
    acos = round(spend / revenue * 100, 1) if revenue > 0 else None
    impressions = random.randint(3000, 15000)
    if status == "reduced":
        impressions = int(impressions * 0.6)
    return {
        "spend": spend,
        "revenue": revenue,
        "impressions": impressions,
        "acos": acos,
        "credit": round(random.uniform(100, 400), 2) if platform == "shopee" else None,
    }


@router.post("/seed")
async def seed(
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_active_user)],
) -> dict:
    """Idempotent: accounts + base schedules + 30 days of metrics + recent
    decisions + 4 learned patterns per account. Re-runs are no-ops once
    the accounts exist."""
    existing = (
        await session.execute(select(func.count()).select_from(MarketingAccount))
    ).scalar_one()
    if existing == 0:
        for name, platform, dept, target, budget, credit in _MOCK_ACCOUNTS:
            session.add(
                MarketingAccount(
                    name=name, platform=platform, department=dept,
                    acos_target=target, daily_budget=float(budget),
                    agent_enabled=True, status="active",
                    credit_balance=credit, current_intensity=50,
                )
            )
        await session.commit()
        # Default schedules: 11–13 + 18–22 every day.
        accounts = (
            await session.execute(select(MarketingAccount))
        ).scalars().all()
        for acc in accounts:
            for dow in range(7):
                session.add(MarketingSchedule(account_id=acc.id, day_of_week=dow, start_hour=11, end_hour=13))
                session.add(MarketingSchedule(account_id=acc.id, day_of_week=dow, start_hour=18, end_hour=22))
        await session.commit()

    accounts = (
        await session.execute(
            select(MarketingAccount).order_by(MarketingAccount.platform, MarketingAccount.name)
        )
    ).scalars().all()

    # 30d metrics (4 snapshots per day) — skip accounts that already have >20 rows.
    now = datetime.now(UTC)
    metrics_created = 0
    for acc in accounts:
        n = (
            await session.execute(
                select(func.count())
                .select_from(MarketingMetric)
                .where(MarketingMetric.account_id == acc.id)
            )
        ).scalar_one()
        if n > 20:
            continue
        for days_back in range(30, 0, -1):
            for h in (9, 13, 18, 22):
                ts = (now - timedelta(days=days_back)).replace(
                    hour=h, minute=0, second=0, microsecond=0
                )
                dow = ts.weekday()
                base = 5.0 if h in (13, 18, 22) else 2.5
                if dow in (4, 5):
                    base *= 1.4
                elif dow == 0:
                    base *= 0.7
                if acc.department == "mala":
                    base *= 0.85
                elif acc.department == "eletro":
                    base *= 1.15
                sales = max(0.0, base + random.gauss(0, base * 0.3))
                impressions = int(sales * random.uniform(80, 150))
                clicks = int(impressions * random.uniform(0.02, 0.08))
                spend = round(clicks * random.uniform(0.3, 1.2), 2)
                revenue = round(sales * random.uniform(80, 200), 2)
                acos = round(spend / revenue * 100, 1) if revenue > 0 else None
                intensity = min(100, int(40 + (sales / max(1, base)) * 30))
                session.add(
                    MarketingMetric(
                        account_id=acc.id, timestamp=ts,
                        spend=spend, revenue=revenue,
                        impressions=impressions, clicks=clicks,
                        orders=int(sales), acos=acos, intensity=intensity,
                    )
                )
                metrics_created += 1
    await session.commit()

    # Patterns + recent decisions
    patterns_created = 0
    decisions_created = 0
    for acc in accounts:
        n_patt = (
            await session.execute(
                select(func.count())
                .select_from(MarketingPattern)
                .where(MarketingPattern.account_id == acc.id)
            )
        ).scalar_one()
        if n_patt == 0:
            for ptype, desc, conf, data in _MOCK_PATTERNS:
                session.add(
                    MarketingPattern(
                        account_id=acc.id, pattern_type=ptype, description=desc,
                        confidence=conf, data=data,
                        discovered_at=now - timedelta(days=random.randint(3, 30)),
                    )
                )
                patterns_created += 1
        n_dec = (
            await session.execute(
                select(func.count())
                .select_from(MarketingDecision)
                .where(MarketingDecision.account_id == acc.id)
            )
        ).scalar_one()
        if n_dec < 5:
            for hours_back in (1, 4, 7, 12, 22):
                ts = now - timedelta(hours=hours_back)
                intensity = random.randint(15, 95)
                if intensity > 75:
                    action = "increase_budget"
                    reasoning = f"Mercado em pico ({intensity}%). Aumentando budget."
                elif intensity < 25:
                    action = "disable_all"
                    reasoning = f"Mercado fraco ({intensity}%). Desligando para economizar."
                else:
                    action = "no_action"
                    reasoning = f"Mercado normal ({intensity}%). Mantendo configuração."
                session.add(
                    MarketingDecision(
                        account_id=acc.id, timestamp=ts, action=action,
                        reasoning=reasoning, params={},
                        market_intensity=intensity,
                        in_base_window=random.choice([True, False]),
                        acos_at_time=round(random.uniform(2, 14), 1),
                    )
                )
                decisions_created += 1
    await session.commit()

    # Campaigns + their 7d roll-up metrics. Skip accounts that already have a
    # campaign with the same name so re-running seed doesn't duplicate them.
    by_key: dict[tuple[str, str, str], MarketingAccount] = {
        (a.name, a.platform, a.department): a for a in accounts
    }
    campaigns_created = 0
    for name, platform, dept, camp_name, status_ in _MOCK_CAMPAIGNS:
        acc = by_key.get((name, platform, dept))
        if acc is None:
            continue
        already = (
            await session.execute(
                select(func.count())
                .select_from(MarketingCampaign)
                .where(
                    and_(
                        MarketingCampaign.account_id == acc.id,
                        MarketingCampaign.name == camp_name,
                    )
                )
            )
        ).scalar_one()
        if already:
            continue
        metrics_blob = _mock_campaign_metrics(platform, status_)
        session.add(
            MarketingCampaign(
                account_id=acc.id,
                name=camp_name,
                status=status_,
                **metrics_blob,
            )
        )
        campaigns_created += 1
    await session.commit()

    return {
        "status": "ok",
        "accounts": len(accounts),
        "metrics_created": metrics_created,
        "patterns_created": patterns_created,
        "decisions_created": decisions_created,
        "campaigns_created": campaigns_created,
    }
