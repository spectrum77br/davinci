"""Schedule reconciler — the control loop behind the automatic agenda.

Every tick (≈1 min on the agent node) this compares, per schedule-enabled
account, the DESIRED state in BRT (`scheduling.desired_state`) against the
ACTUAL campaign state, and enqueues ONE whole-account command to correct a
drift. It does NOT call any marketplace API — it only writes to the outbox;
the consumer executes. That split is what makes it safe to run every minute
and robust to restarts: state is re-derived from scratch each tick, so a
missed or crashed run just reconverges on the next one.

Dedup: if the account already has a `pending`/`claimed` command, we skip —
otherwise a minute-by-minute tick would pile up duplicate pause/resume
commands while the consumer (throttled by Shopee) works through them. Once
the consumer runs and the next sync refreshes MarketingCampaign.status,
desired==actual and the loop goes quiet.
"""
from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.marketing import (
    MarketingAccount,
    MarketingCampaign,
    MarketingCommand,
    MarketingSchedule,
)
from app.services.marketing.scheduling import desired_state
from datetime import UTC, datetime

logger = structlog.get_logger()

_RECONCILABLE_PLATFORMS = frozenset({"shopee", "ml"})


async def _has_open_command(session: AsyncSession, account_id) -> bool:
    """True if a pending/claimed command already exists for the account —
    in-flight work we must not duplicate."""
    n = (
        await session.execute(
            select(func.count())
            .select_from(MarketingCommand)
            .where(
                MarketingCommand.account_id == account_id,
                MarketingCommand.status.in_(("pending", "claimed")),
            )
        )
    ).scalar_one()
    return n > 0


async def reconcile_schedules(session: AsyncSession) -> dict[str, Any]:
    """One reconciliation pass. Returns a summary the cron logs."""
    now = datetime.now(UTC)
    accounts = (
        await session.execute(
            select(MarketingAccount).where(
                MarketingAccount.schedule_enabled.is_(True),
                MarketingAccount.platform.in_(tuple(_RECONCILABLE_PLATFORMS)),
            )
        )
    ).scalars().all()

    checked = enqueued = 0
    for acc in accounts:
        checked += 1
        schedules = (
            await session.execute(
                select(MarketingSchedule).where(MarketingSchedule.account_id == acc.id)
            )
        ).scalars().all()
        desired = desired_state(acc, list(schedules), now)

        # Actual state from campaigns we can actually toggle (external_id set).
        camps = (
            await session.execute(
                select(MarketingCampaign).where(MarketingCampaign.account_id == acc.id)
            )
        ).scalars().all()
        toggleable = [c for c in camps if c.external_id]
        if not toggleable:
            continue
        has_active = any(c.status == "active" for c in toggleable)
        has_off = any(c.status in ("paused", "off") for c in toggleable)

        # desired 'on'  → drift if any campaign is paused/off  → resume
        # desired 'off' → drift if any campaign is active      → pause
        if desired == "on" and has_off:
            action = "resume"
        elif desired == "off" and has_active:
            action = "pause"
        else:
            continue  # already converged

        if await _has_open_command(session, acc.id):
            continue  # in-flight command will move actual toward desired

        session.add(
            MarketingCommand(
                account_id=acc.id,
                campaign_external_id=None,  # whole account
                platform=acc.platform,
                action=action,
                payload={},
                status="pending",
                source="schedule",
            )
        )
        enqueued += 1
        logger.info(
            "marketing_reconcile_enqueue",
            account_id=str(acc.id), desired=desired, action=action,
        )

    await session.commit()
    return {"checked": checked, "enqueued": enqueued}
