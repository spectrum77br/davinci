"""Consume the marketing_commands outbox on the dedicated agent node.

`consume_pending_commands` claims the oldest `pending` rows with
`FOR UPDATE SKIP LOCKED` (so two workers never grab the same command),
flips them to `claimed`, then runs each through the platform's
`edit_campaign`. A command targets ONE campaign (`campaign_external_id`
set) or ALL of the account's campaigns (NULL).

Action map:
  pause             → edit_campaign(status= paused)
  resume            → edit_campaign(status= ongoing[shopee]/active[ml])
  set_budget        → edit_campaign(daily_budget= payload.budget)
  adjust_budget_pct → read current budget → *(1 + pct/100) → edit_campaign

Shopee throttle: honours the SAME global Redis cooldown the sync uses
(`shopee_sync._is_on_cooldown`) and paces calls with `_inter_call_delay()`.
A `ShopeeAdsRateLimit` does NOT fail the command — it re-queues it
(`pending`) and arms the cooldown so the next tick retries after the
partner-wide throttle clears. Everything else (bad creds, unknown action)
fails the command with the error in `result`.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.integration import Integration
from app.models.marketing import MarketingAccount, MarketingCampaign, MarketingCommand
from app.security.cipher import decrypt_json, encrypt_json
from app.services.marketing.shopee_sync import (
    _inter_call_delay,
    _is_on_cooldown,
    _set_cooldown,
)
from app.services.ml_ads import MLAdsClient, MLAdsError
from app.services.marketplaces.shopee import ShopeeClient
from app.services.shopee_ads import ShopeeAdsClient, ShopeeAdsError, ShopeeAdsRateLimit

logger = structlog.get_logger()

_VALID_ACTIONS = frozenset({"pause", "resume", "set_budget", "adjust_budget_pct"})


async def consume_pending_commands(
    session: AsyncSession, *, max_batch: int = 5
) -> dict[str, Any]:
    """Claim + execute up to `max_batch` pending commands. Returns a small
    summary the cron can log."""
    claimed = (
        await session.execute(
            select(MarketingCommand)
            .where(MarketingCommand.status == "pending")
            .order_by(MarketingCommand.created_at.asc())
            .limit(max_batch)
            .with_for_update(skip_locked=True)
        )
    ).scalars().all()

    if not claimed:
        return {"claimed": 0, "done": 0, "failed": 0, "requeued": 0}

    now = datetime.now(UTC)
    for cmd in claimed:
        cmd.status = "claimed"
        cmd.claimed_at = now
        cmd.attempts += 1
    # Commit the claim so a crash mid-execution doesn't leave the rows
    # invisible-but-locked; they'll be re-picked as 'claimed' is handled
    # below (a follow-up sync corrects state, the reconciler re-enqueues).
    await session.commit()

    done = failed = requeued = 0
    for cmd in claimed:
        outcome = await _execute_command(session, cmd)
        if outcome == "done":
            done += 1
        elif outcome == "requeued":
            requeued += 1
        else:
            failed += 1
        await session.commit()

    logger.info(
        "marketing_commands_consumed",
        claimed=len(claimed), done=done, failed=failed, requeued=requeued,
    )
    return {"claimed": len(claimed), "done": done, "failed": failed, "requeued": requeued}


def _finish(cmd: MarketingCommand, status: str, result: str) -> str:
    cmd.status = status
    cmd.result = result[:2000]
    if status in ("done", "failed"):
        cmd.completed_at = datetime.now(UTC)
    return status


def _requeue(cmd: MarketingCommand, reason: str) -> str:
    """Put the command back in the queue without burning an attempt (the
    claim incremented it; a throttle bounce isn't a real try)."""
    cmd.status = "pending"
    cmd.claimed_at = None
    cmd.attempts = max(0, cmd.attempts - 1)
    cmd.result = reason[:2000]
    return "requeued"


async def _target_external_ids(
    session: AsyncSession, cmd: MarketingCommand
) -> list[str]:
    if cmd.campaign_external_id:
        return [cmd.campaign_external_id]
    camps = (
        await session.execute(
            select(MarketingCampaign).where(MarketingCampaign.account_id == cmd.account_id)
        )
    ).scalars().all()
    return [c.external_id for c in camps if c.external_id]


async def _execute_command(session: AsyncSession, cmd: MarketingCommand) -> str:
    if cmd.action not in _VALID_ACTIONS:
        return _finish(cmd, "failed", f"unknown_action:{cmd.action}")

    account = await session.get(MarketingAccount, cmd.account_id)
    if account is None:
        return _finish(cmd, "failed", "account_not_found")
    if account.integration_id is None:
        return _finish(cmd, "failed", "account_has_no_integration")
    integration = await session.get(Integration, account.integration_id)
    if integration is None:
        return _finish(cmd, "failed", "integration_not_found")

    platform = cmd.platform or account.platform
    ext_ids = await _target_external_ids(session, cmd)
    if not ext_ids:
        # Whole-account command on an account with no synced campaigns yet —
        # nothing to do, but not an error (the next sync may populate them).
        return _finish(cmd, "done", "no_campaigns")

    creds = decrypt_json(integration.credentials)

    async def _persist_refreshed_creds(new_creds: dict) -> None:
        integration.credentials = encrypt_json(new_creds)
        expires_at = new_creds.get("expires_at")
        if expires_at:
            integration.token_expires_at = datetime.fromtimestamp(int(expires_at), tz=UTC)
        await session.flush()

    try:
        if platform == "shopee":
            return await _execute_shopee(cmd, creds, _persist_refreshed_creds, ext_ids)
        if platform == "ml":
            return await _execute_ml(cmd, creds, _persist_refreshed_creds, ext_ids)
        return _finish(cmd, "failed", f"unsupported_platform:{platform}")
    except ShopeeAdsRateLimit as e:
        await _set_cooldown(f"command:{e.code}")
        return _requeue(cmd, f"rate_limited:{e.code}")
    except (ShopeeAdsError, MLAdsError) as e:
        return _finish(cmd, "failed", f"{e.code}: {e.message}")
    except Exception as e:  # noqa: BLE001
        logger.error("marketing_command_failed", command_id=str(cmd.id), err=str(e)[:300])
        return _finish(cmd, "failed", f"{type(e).__name__}: {str(e)[:300]}")


async def _execute_shopee(
    cmd: MarketingCommand, creds: dict, on_refresh, ext_ids: list[str]
) -> str:
    if await _is_on_cooldown():
        return _requeue(cmd, "shopee_on_cooldown")

    client = ShopeeAdsClient(ShopeeClient(creds, on_token_refresh=on_refresh))
    pct = float(cmd.payload.get("pct", 0)) if cmd.action == "adjust_budget_pct" else 0.0
    budget = cmd.payload.get("budget")
    applied = 0
    for i, ext in enumerate(ext_ids):
        if i > 0:
            await asyncio.sleep(_inter_call_delay())
        cid = int(ext)
        if cmd.action == "pause":
            await client.edit_campaign(cid, status="paused")
        elif cmd.action == "resume":
            await client.edit_campaign(cid, status="ongoing")
        elif cmd.action == "set_budget":
            await client.edit_campaign(cid, daily_budget=float(budget))
        elif cmd.action == "adjust_budget_pct":
            info = await client.get_campaign_settings([cid])
            current = info[0].daily_budget if info else None
            if current is None:
                continue
            await asyncio.sleep(_inter_call_delay())
            await client.edit_campaign(cid, daily_budget=round(current * (1 + pct / 100), 2))
        applied += 1
    return _finish(cmd, "done", f"shopee:{cmd.action}:{applied}/{len(ext_ids)}")


async def _execute_ml(
    cmd: MarketingCommand, creds: dict, on_refresh, ext_ids: list[str]
) -> str:
    client = MLAdsClient(creds, on_token_refresh=on_refresh)
    pct = float(cmd.payload.get("pct", 0)) if cmd.action == "adjust_budget_pct" else 0.0
    budget = cmd.payload.get("budget")

    # adjust_budget_pct needs each campaign's current budget; ML exposes it
    # only on the metadata list, so fetch it once and index by id.
    budget_by_id: dict[str, float | None] = {}
    if cmd.action == "adjust_budget_pct":
        for c in await client.list_campaigns_metadata():
            cid = str(c.get("id") or c.get("campaign_id") or "")
            if cid:
                budget_by_id[cid] = (
                    float(c["daily_budget"]) if c.get("daily_budget") is not None else None
                )

    applied = 0
    for ext in ext_ids:
        if cmd.action == "pause":
            await client.edit_campaign(ext, status="paused")
        elif cmd.action == "resume":
            await client.edit_campaign(ext, status="active")
        elif cmd.action == "set_budget":
            await client.edit_campaign(ext, daily_budget=float(budget))
        elif cmd.action == "adjust_budget_pct":
            current = budget_by_id.get(ext)
            if current is None:
                continue
            await client.edit_campaign(ext, daily_budget=round(current * (1 + pct / 100), 2))
        applied += 1
    return _finish(cmd, "done", f"ml:{cmd.action}:{applied}/{len(ext_ids)}")
