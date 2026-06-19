"""Command consumer + schedule reconciler (DB-backed).

The Shopee Ads HTTP client is faked end-to-end (no network): we patch the
client constructors + cooldown/throttle helpers inside
`app.services.marketing.commands` so the consumer exercises its full
claim→execute→stamp path against an in-memory recorder.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import select, text

from app.models import IntegrationPlatform
from app.models.marketing import (
    MarketingAccount,
    MarketingCampaign,
    MarketingCommand,
)
from app.models.integration import Integration
import app.services.marketing.commands as commands_mod
from app.services.marketing.commands import consume_pending_commands
from app.services.marketing.reconcile import reconcile_schedules
from app.services.shopee_ads import ShopeeAdsRateLimit


async def _wipe_marketing(db) -> None:
    for tbl in ("marketing_commands", "marketing_campaigns", "marketing_schedules", "marketing_accounts"):
        await db.execute(text(f"DELETE FROM {tbl}"))  # noqa: S608
    await db.commit()


async def _shopee_account(db, make_user, *, schedule_enabled=False, override_action=None):
    user = await make_user()
    integ = Integration(
        user_id=user.id, platform=IntegrationPlatform.SHOPEE, name="loja",
        credentials=b"ignored-since-decrypt-is-patched", status="active",
    )
    db.add(integ)
    await db.flush()
    acc = MarketingAccount(
        integration_id=integ.id, name="loja", platform="shopee",
        department="geral", acos_target=7.0,
        schedule_enabled=schedule_enabled, override_action=override_action,
    )
    db.add(acc)
    await db.flush()
    return user, integ, acc


class _FakeAds:
    """Records edit_campaign calls; serves a fixed budget for adjust."""

    instances: list["_FakeAds"] = []

    def __init__(self, *_a, **_k):
        self.calls: list[dict] = []
        _FakeAds.instances.append(self)

    async def edit_campaign(self, cid, *, status=None, daily_budget=None):
        self.calls.append({"cid": cid, "status": status, "daily_budget": daily_budget})
        return {"ok": True}

    async def get_campaign_settings(self, ids):
        return [SimpleNamespace(daily_budget=100.0) for _ in ids]


class _RateLimitedAds(_FakeAds):
    async def edit_campaign(self, cid, *, status=None, daily_budget=None):
        raise ShopeeAdsRateLimit("ads_rate_limit_total_api", "throttled", "/edit")


@pytest.fixture
def patch_shopee(monkeypatch):
    """Patch the consumer's client + throttle helpers. Returns a dict the
    test can inspect (cooldown_armed flag)."""
    state = {"cooldown_armed": False}
    _FakeAds.instances = []

    async def _no_cooldown():
        return False

    async def _arm_cooldown(reason):
        state["cooldown_armed"] = True

    monkeypatch.setattr(commands_mod, "decrypt_json", lambda blob: {})
    monkeypatch.setattr(commands_mod, "ShopeeClient", lambda *a, **k: object())
    monkeypatch.setattr(commands_mod, "ShopeeAdsClient", _FakeAds)
    monkeypatch.setattr(commands_mod, "_is_on_cooldown", _no_cooldown)
    monkeypatch.setattr(commands_mod, "_set_cooldown", _arm_cooldown)
    monkeypatch.setattr(commands_mod, "_inter_call_delay", lambda: 0.0)
    return state


async def _add_campaign(db, acc, ext_id, status="active"):
    c = MarketingCampaign(
        account_id=acc.id, name=f"c{ext_id}", external_id=ext_id, status=status,
    )
    db.add(c)
    await db.flush()
    return c


# ─── consumer ──────────────────────────────────────────────────────────


async def test_pause_all_campaigns(db, make_user, patch_shopee):
    await _wipe_marketing(db)
    _u, _i, acc = await _shopee_account(db, make_user)
    await _add_campaign(db, acc, "111")
    await _add_campaign(db, acc, "222")
    cmd = MarketingCommand(
        account_id=acc.id, platform="shopee", action="pause", payload={},
        status="pending", source="manual",
    )
    db.add(cmd)
    await db.commit()

    summary = await consume_pending_commands(db)
    assert summary["done"] == 1

    fake = _FakeAds.instances[-1]
    assert len(fake.calls) == 2  # both campaigns
    assert all(c["status"] == "paused" for c in fake.calls)

    done = (await db.execute(select(MarketingCommand).where(MarketingCommand.id == cmd.id))).scalar_one()
    assert done.status == "done"
    assert done.completed_at is not None


async def test_set_budget_single_campaign(db, make_user, patch_shopee):
    await _wipe_marketing(db)
    _u, _i, acc = await _shopee_account(db, make_user)
    await _add_campaign(db, acc, "111")
    await _add_campaign(db, acc, "222")
    cmd = MarketingCommand(
        account_id=acc.id, platform="shopee", action="set_budget",
        campaign_external_id="222", payload={"budget": 55.5},
        status="pending", source="manual",
    )
    db.add(cmd)
    await db.commit()

    await consume_pending_commands(db)
    fake = _FakeAds.instances[-1]
    assert fake.calls == [{"cid": 222, "status": None, "daily_budget": 55.5}]


async def test_adjust_budget_pct(db, make_user, patch_shopee):
    await _wipe_marketing(db)
    _u, _i, acc = await _shopee_account(db, make_user)
    await _add_campaign(db, acc, "111")
    cmd = MarketingCommand(
        account_id=acc.id, platform="shopee", action="adjust_budget_pct",
        campaign_external_id="111", payload={"pct": 20},
        status="pending", source="manual",
    )
    db.add(cmd)
    await db.commit()

    await consume_pending_commands(db)
    fake = _FakeAds.instances[-1]
    # current 100 * 1.20 = 120.0
    assert fake.calls[-1] == {"cid": 111, "status": None, "daily_budget": 120.0}


async def test_rate_limit_keeps_command_pending(db, make_user, monkeypatch, patch_shopee):
    await _wipe_marketing(db)
    monkeypatch.setattr(commands_mod, "ShopeeAdsClient", _RateLimitedAds)
    _u, _i, acc = await _shopee_account(db, make_user)
    await _add_campaign(db, acc, "111")
    cmd = MarketingCommand(
        account_id=acc.id, platform="shopee", action="pause", payload={},
        status="pending", source="manual",
    )
    db.add(cmd)
    await db.commit()

    summary = await consume_pending_commands(db)
    assert summary["requeued"] == 1
    assert summary["done"] == 0

    again = (await db.execute(select(MarketingCommand).where(MarketingCommand.id == cmd.id))).scalar_one()
    assert again.status == "pending"  # NOT failed — retried next tick
    assert again.completed_at is None
    assert patch_shopee["cooldown_armed"] is True


# ─── reconciler ──────────────────────────────────────────────────────────


async def test_reconcile_enqueues_resume_on_drift(db, make_user):
    await _wipe_marketing(db)
    # override 'resume' → desired ON regardless of clock; campaign paused → drift.
    _u, _i, acc = await _shopee_account(db, make_user, schedule_enabled=True, override_action="resume")
    await _add_campaign(db, acc, "111", status="paused")
    await db.commit()

    r = await reconcile_schedules(db)
    assert r["enqueued"] == 1
    cmds = (await db.execute(select(MarketingCommand).where(MarketingCommand.account_id == acc.id))).scalars().all()
    assert len(cmds) == 1
    assert cmds[0].action == "resume"
    assert cmds[0].source == "schedule"
    assert cmds[0].campaign_external_id is None


async def test_reconcile_no_drift_no_enqueue(db, make_user):
    await _wipe_marketing(db)
    _u, _i, acc = await _shopee_account(db, make_user, schedule_enabled=True, override_action="resume")
    await _add_campaign(db, acc, "111", status="active")  # already on → no drift
    await db.commit()

    r = await reconcile_schedules(db)
    assert r["enqueued"] == 0


async def test_reconcile_dedups_open_command(db, make_user):
    await _wipe_marketing(db)
    _u, _i, acc = await _shopee_account(db, make_user, schedule_enabled=True, override_action="pause")
    await _add_campaign(db, acc, "111", status="active")  # desired off, active → drift → pause
    await db.commit()

    first = await reconcile_schedules(db)
    assert first["enqueued"] == 1
    # Second pass: a pending command already exists → must not duplicate.
    second = await reconcile_schedules(db)
    assert second["enqueued"] == 0
    cmds = (await db.execute(select(MarketingCommand).where(MarketingCommand.account_id == acc.id))).scalars().all()
    assert len(cmds) == 1
