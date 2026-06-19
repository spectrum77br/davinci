"""Schedule → desired state, computed in Brasília time.

The schedule windows (`MarketingSchedule`) are authored in BRT
(America/Sao_Paulo) — a window "19h–24h" means 19:00–24:00 São Paulo time,
NOT UTC. `agent.is_in_scheduled_window` reads `now.weekday()`/`now.hour`
straight off whatever datetime it's handed, so the ONLY thing that makes it
correct is feeding it a BRT-localized `now`. The agent's mock cycle passes
UTC (acceptable for mock signals); the reconciler that actually pauses/
resumes real campaigns MUST pass BRT — hence this module.

`desired_state` is the single source of truth for "should this account's
campaigns be ON or OFF right now", layering the manual override on top of
the windows. `describe_state` adds the next ON/OFF flip for the dashboard.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.models.marketing import MarketingAccount, MarketingSchedule
from app.services.marketing.agent import is_in_scheduled_window

SP_TZ = ZoneInfo("America/Sao_Paulo")


def _override_active(account: MarketingAccount, now_utc: datetime) -> bool:
    """True if a manual override is set AND still in force (no expiry, or
    expiry in the future)."""
    if not account.override_action:
        return False
    if account.override_until is None:
        return True
    return account.override_until > now_utc


def desired_state(
    account: MarketingAccount,
    schedules: list[MarketingSchedule],
    now_utc: datetime,
) -> str:
    """'on' | 'off' for this account at `now_utc`.

    1. Active manual override wins: 'pause' → 'off', 'resume' → 'on'.
    2. Otherwise the BRT windows decide: 'on' inside any MarketingSchedule
       block (evaluated in America/Sao_Paulo), else 'off'.
    """
    if _override_active(account, now_utc):
        return "off" if account.override_action == "pause" else "on"
    now_brt = now_utc.astimezone(SP_TZ)
    return "on" if is_in_scheduled_window(schedules, now_brt) else "off"


def next_transition(
    account: MarketingAccount,
    schedules: list[MarketingSchedule],
    now_utc: datetime,
    *,
    horizon_hours: int = 24 * 8,
) -> datetime | None:
    """First UTC instant in the next `horizon_hours` where `desired_state`
    flips. Scans on the hour boundary (windows are hour-granular). Returns
    None if the state never changes within the horizon (e.g. an open-ended
    override, or a 24/7 schedule).

    Used only for the dashboard hint ("religa 19:00" / "ligado até 15:00")
    — the reconciler itself never needs it.
    """
    current = desired_state(account, schedules, now_utc)
    now_brt = now_utc.astimezone(SP_TZ)
    # Start at the next whole hour in BRT so we report the boundary, not "now".
    probe = (now_brt + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    for _ in range(horizon_hours):
        probe_utc = probe.astimezone(now_utc.tzinfo) if now_utc.tzinfo else probe
        if desired_state(account, schedules, probe_utc) != current:
            return probe_utc
        probe += timedelta(hours=1)
    return None


def describe_state(
    account: MarketingAccount,
    schedules: list[MarketingSchedule],
    now_utc: datetime,
) -> dict:
    """Dashboard payload: current desired state + when it next flips +
    whether an override is in force. Times are returned as ISO UTC; the
    frontend renders them in BRT."""
    state = desired_state(account, schedules, now_utc)
    nxt = next_transition(account, schedules, now_utc)
    return {
        "state": state,
        "schedule_enabled": account.schedule_enabled,
        "override_action": account.override_action,
        "override_until": account.override_until.isoformat() if account.override_until else None,
        "override_active": _override_active(account, now_utc),
        "next_transition": nxt.isoformat() if nxt else None,
        # 'on' → the flip turns it off, and vice-versa.
        "next_state": ("off" if state == "on" else "on") if nxt else None,
    }
