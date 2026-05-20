"""Marketing agent — per-(platform, department) decision cycle.

Pipeline (one cycle per MarketingAccount, runs every 15 min):
  1. collect_signals  → mock spend/revenue/impressions/credit for "now"
  2. is_in_window     → check guaranteed-on MarketingSchedule blocks
  3. calculate_intensity → 0..100 from sales + impressions ratios
  4. make_decision    → rules listed in PARTE 2 of the spec
  5. persist          → MarketingDecision + MarketingMetric + status mirror
  6. check_alerts     → low-credit Telegram alert (Shopee only, ≤2 days)
  7. detect_patterns  → opportunistic mock pattern emission

The whole thing is mock-only: signals are deterministic-ish by hour/day so the
dashboard has a shape, and decisions follow the published rule table. Real
marketplace + Anthropic hooks plug into `collect_signals` / `make_decision`
without changing the cycle plumbing.
"""
from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from uuid import UUID

import structlog
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session_scope
from app.models.marketing import (
    MarketingAccount,
    MarketingDecision,
    MarketingMetric,
    MarketingPattern,
    MarketingSchedule,
)
from app.services.telegram import TelegramClient

logger = structlog.get_logger()


def _hourly_base(hour: int, dow: int, department: str) -> float:
    """Mock baseline orders/hour shape — lunch+evening peaks, slow nights,
    Fri/Sat heavier, Monday weaker. Mala/Eletro skew slightly different from
    Celular so the dashboard feels distinct between tabs."""
    base = 2.0
    if 11 <= hour <= 13 or 18 <= hour <= 22:
        base = 5.0
    elif 0 <= hour <= 6:
        base = 0.5
    if dow in (4, 5):
        base *= 1.4
    elif dow == 0:
        base *= 0.7
    if department == "mala":
        base *= 0.85
    elif department == "eletro":
        base *= 1.15
    return base


def is_in_scheduled_window(schedules: list[MarketingSchedule], now: datetime) -> bool:
    dow = now.weekday()
    hour = now.hour
    for s in schedules:
        if s.day_of_week != dow:
            continue
        if s.end_hour <= s.start_hour:
            if hour >= s.start_hour or hour < s.end_hour:
                return True
        elif s.start_hour <= hour < s.end_hour:
            return True
    return False


def calculate_intensity(
    sales_last_hour: float,
    avg_sales_this_hour: float,
    impressions_last_hour: float,
    avg_impressions_this_hour: float,
) -> int:
    sales_ratio = sales_last_hour / avg_sales_this_hour if avg_sales_this_hour > 0 else 1.0
    impressions_ratio = (
        impressions_last_hour / avg_impressions_this_hour
        if avg_impressions_this_hour > 0
        else 1.0
    )
    raw = (sales_ratio * 0.7 + impressions_ratio * 0.3) * 50
    return min(100, max(0, int(raw)))


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


async def collect_signals(account: MarketingAccount) -> dict:
    """Return a mock snapshot for the account at "now"."""
    now = datetime.now(UTC)
    hour = now.hour
    dow = now.weekday()
    base = _hourly_base(hour, dow, account.department)

    sales_last_hour = max(0.0, base + random.gauss(0, base * 0.3))
    impressions_last_hour = sales_last_hour * random.uniform(80, 150)
    clicks_last_hour = impressions_last_hour * random.uniform(0.02, 0.08)
    spend_last_hour = clicks_last_hour * random.uniform(0.3, 1.2)
    avg_sales_this_hour = base * 0.9
    avg_impressions_this_hour = avg_sales_this_hour * 100

    revenue_today = sales_last_hour * random.uniform(80, 200) * max(1, hour)
    spend_today = spend_last_hour * max(1, hour)
    acos_current = (spend_today / revenue_today * 100) if revenue_today > 0 else 0.0

    # 7d daily spend average used by the low-credit alert (Shopee).
    avg_daily_spend = spend_today * 1.05 if hour >= 12 else spend_today * 2.2

    sales_ratio = (sales_last_hour / avg_sales_this_hour * 100) if avg_sales_this_hour > 0 else 100
    return {
        "sales_last_hour": round(sales_last_hour, 1),
        "avg_sales_this_hour": round(avg_sales_this_hour, 1),
        "sales_ratio": round(sales_ratio, 0),
        "impressions_last_hour": int(impressions_last_hour),
        "avg_impressions_this_hour": int(avg_impressions_this_hour),
        "clicks_last_hour": int(clicks_last_hour),
        "spend_last_hour": round(spend_last_hour, 2),
        "spend_today": round(spend_today, 2),
        "revenue_today": round(revenue_today, 2),
        "acos_current": round(acos_current, 1),
        "credit_balance": account.credit_balance,
        "avg_daily_spend": round(avg_daily_spend, 2),
        "hour": hour,
        "day_of_week": dow,
    }


def make_decision(
    *,
    account: MarketingAccount,
    signals: dict,
    in_base_window: bool,
    market_intensity: int,
) -> dict:
    """Rule-based decision. Spec PARTE 2 verbatim."""
    acos_current = signals["acos_current"]
    acos_target = account.acos_target
    sales_ratio = signals["sales_ratio"]

    # 1. Peak outside base → enable
    if not in_base_window and market_intensity > 70:
        return {
            "action": "enable_all",
            "reasoning": (
                f"Mercado aquecido ({market_intensity}%) fora do horário base. "
                f"Vendas {sales_ratio:.0f}% acima da média. Ligando ads para aproveitar."
            ),
            "params": {},
        }
    # 2. Dead outside base → disable
    if not in_base_window and market_intensity < 25:
        return {
            "action": "disable_all",
            "reasoning": (
                f"Mercado fraco ({market_intensity}%). Fora do horário base. "
                f"Desligando para economizar budget."
            ),
            "params": {},
        }
    # 3. ACOS way over target → reduce
    if acos_current > acos_target * 2:
        return {
            "action": "decrease_budget",
            "reasoning": (
                f"ACOS subiu para {acos_current:.1f}% (target {acos_target}%). "
                f"Reduzindo budget até normalizar."
            ),
            "params": {"budget_change_pct": -20},
        }
    # 4. ACOS excellent + heated → increase
    if acos_current < acos_target * 0.6 and market_intensity > 50:
        return {
            "action": "increase_budget",
            "reasoning": (
                f"ACOS excelente ({acos_current:.1f}%) e mercado aquecido "
                f"({market_intensity}%). Aumentando budget para vender mais."
            ),
            "params": {"budget_change_pct": 20},
        }
    # 5. Strong peak → increase
    if market_intensity > 80:
        return {
            "action": "increase_budget",
            "reasoning": (
                f"Mercado em pico ({market_intensity}%). Vendas {sales_ratio:.0f}% "
                f"acima da média. Aumentando budget."
            ),
            "params": {"budget_change_pct": 30},
        }
    # 6. Inside base, normal → no_action
    if in_base_window:
        return {
            "action": "no_action",
            "reasoning": (
                f"Mercado {_assessment(market_intensity)} ({market_intensity}%). "
                f"Dentro do horário base. Mantendo configuração atual."
            ),
            "params": {},
        }
    # 7. Outside base, mid market → keep off
    return {
        "action": "disable_all",
        "reasoning": (
            f"Fora do horário base e mercado não justifica ({market_intensity}%). "
            f"Mantendo desligado."
        ),
        "params": {},
    }


async def check_alerts(account: MarketingAccount, signals: dict) -> str | None:
    """Telegram alert for Shopee when credit < 2 days of average spend.

    Returns the alert message (for the dashboard) or None. Telegram send is
    best-effort via TelegramClient.safe_send; missing creds just log.
    """
    if account.platform != "shopee":
        return None
    credit = signals.get("credit_balance")
    daily_avg = signals.get("avg_daily_spend") or 0
    if credit is None or daily_avg <= 0:
        return None
    days_remaining = credit / daily_avg
    if days_remaining > 2:
        return None
    msg = (
        f"⚠️ CRÉDITO BAIXO — {account.name} ({account.department})\n"
        f"Crédito restante: R${credit:.2f}\n"
        f"Gasto médio/dia: R${daily_avg:.2f}\n"
        f"Estimativa: acaba em {days_remaining:.1f} dias\n"
        f"→ Recarregar urgente!"
    )
    try:
        tg = TelegramClient()
        await tg.safe_send(msg)
    except Exception as e:  # noqa: BLE001 — TelegramConfigError shouldn't abort the cycle
        logger.info("marketing_telegram_unavailable", err=str(e)[:200])
    return msg


async def detect_patterns(
    session: AsyncSession, account: MarketingAccount, now: datetime
) -> str | None:
    """Mock pattern detector. Looks at the last 4 weeks of the SAME (dow,hour)
    bucket; if sales there are ≥40% above the overall average, emit a
    peak_hour pattern (idempotent: skips if same description already exists)."""
    dow = now.weekday()
    hour = now.hour
    weeks_ago = now - timedelta(weeks=4)
    same_slot_avg = (
        await session.execute(
            select(func.coalesce(func.avg(MarketingMetric.orders), 0.0)).where(
                and_(
                    MarketingMetric.account_id == account.id,
                    MarketingMetric.timestamp >= weeks_ago,
                    func.extract("dow", MarketingMetric.timestamp) == (dow + 1) % 7,
                    func.extract("hour", MarketingMetric.timestamp) == hour,
                )
            )
        )
    ).scalar() or 0
    overall_avg = (
        await session.execute(
            select(func.coalesce(func.avg(MarketingMetric.orders), 0.0)).where(
                and_(
                    MarketingMetric.account_id == account.id,
                    MarketingMetric.timestamp >= weeks_ago,
                )
            )
        )
    ).scalar() or 0
    if overall_avg <= 0 or same_slot_avg < overall_avg * 1.4:
        return None
    ratio = (float(same_slot_avg) / float(overall_avg) - 1) * 100
    days_pt = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"]
    description = f"{days_pt[dow]} {hour}h: vendas {ratio:.0f}% acima da média"

    exists = (
        await session.execute(
            select(MarketingPattern).where(
                and_(
                    MarketingPattern.account_id == account.id,
                    MarketingPattern.description == description,
                )
            )
        )
    ).scalar_one_or_none()
    if exists is not None:
        return None

    session.add(
        MarketingPattern(
            account_id=account.id,
            pattern_type="peak_hour",
            description=description,
            confidence=min(0.95, 0.7 + random.random() * 0.2),
            data={"dow": dow, "hour": hour, "ratio": ratio},
            discovered_at=now,
        )
    )
    return description


async def agent_decision_cycle(account_id: UUID) -> dict | None:
    """One cycle for one MarketingAccount. Used both by the worker cron and
    by the manual trigger endpoint, hence its own session_scope."""
    async with session_scope() as session:
        account = await session.get(MarketingAccount, account_id)
        if account is None or not account.agent_enabled:
            return None

        signals = await collect_signals(account)
        schedules = (
            await session.execute(
                select(MarketingSchedule).where(MarketingSchedule.account_id == account.id)
            )
        ).scalars().all()
        now = datetime.now(UTC)
        in_base = is_in_scheduled_window(list(schedules), now)
        intensity = calculate_intensity(
            signals["sales_last_hour"],
            signals["avg_sales_this_hour"],
            signals["impressions_last_hour"],
            signals["avg_impressions_this_hour"],
        )

        decision = make_decision(
            account=account,
            signals=signals,
            in_base_window=in_base,
            market_intensity=intensity,
        )

        session.add(
            MarketingDecision(
                account_id=account.id,
                timestamp=now,
                action=decision["action"],
                reasoning=decision["reasoning"],
                params=decision.get("params") or {},
                market_intensity=intensity,
                in_base_window=in_base,
                acos_at_time=signals["acos_current"],
            )
        )

        session.add(
            MarketingMetric(
                account_id=account.id,
                timestamp=now,
                spend=signals["spend_last_hour"],
                revenue=signals["revenue_today"] / max(1, signals["hour"]),
                impressions=signals["impressions_last_hour"],
                clicks=signals["clicks_last_hour"],
                orders=int(signals["sales_last_hour"]),
                acos=signals["acos_current"],
                intensity=intensity,
            )
        )

        # Mirror current state on the account row.
        account.current_intensity = intensity
        if decision["action"] == "enable_all":
            account.status = "active"
        elif decision["action"] == "disable_all":
            account.status = "off"
        elif decision["action"] == "decrease_budget":
            account.status = "reduced"
        # increase_budget keeps "active"; no_action leaves status untouched.

        alert = await check_alerts(account, signals)
        pattern = await detect_patterns(session, account, now)

        return {
            "account_id": str(account.id),
            "account_name": account.name,
            "platform": account.platform,
            "department": account.department,
            "intensity": intensity,
            "assessment": _assessment(intensity),
            "decision": decision,
            "in_base_window": in_base,
            "alert": alert,
            "pattern_detected": pattern,
        }
