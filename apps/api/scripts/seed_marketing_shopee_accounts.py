"""Seed the 18 Shopee accounts driven by the LOCAL browser executor (marionete).

These are NOT API-integrated accounts: Shopee's official Ads API is blocked by
the partner quota, so DaVinci only holds the control plane (schedule + outbox)
and the local Mac drives each shop via AdsPower. This script materialises one
MarketingAccount per shop with:

  - platform   = "shopee"
  - department = "geral"        (whole-shop toggle, not per-vertical)
  - integration_id = NULL       (no OAuth integration; browser-driven)
  - agent_enabled  = False      (not the autonomous decision agent)
  - adspower_user_id            (the AdsPower profile the executor opens)
  - schedule_enabled            (True for the shops that run on the BRT agenda)

It also gives each shop a default BRT window of 18:00–22:00 every day (only if
the shop has NO windows yet — so re-running never clobbers windows you tuned in
the UI). Enable/disable and window edits are meant to be done in the dashboard
afterwards; this is just the starting point.

Idempotent: matched by (platform='shopee', department='geral', name). On an
existing row it only refreshes `adspower_user_id` (the stable mapping) and
never touches schedule_enabled/override/etc. that the operator may have changed.

Usage (DRY-RUN by default — prints the plan, writes nothing):
    .venv/bin/python -m scripts.seed_marketing_shopee_accounts
Apply for real:
    .venv/bin/python -m scripts.seed_marketing_shopee_accounts --apply
"""

from __future__ import annotations

import sys

from sqlalchemy import select

from app.db import get_session
from app.models.marketing import MarketingAccount, MarketingSchedule

# (name, adspower_user_id, schedule_enabled) — the 18 marionete shops.
# schedule_enabled=True mirrors the shops that currently run on the agenda;
# the rest start disabled and can be flipped on in the dashboard.
_SHOPEE_ACCOUNTS: list[tuple[str, str, bool]] = [
    ("Mega", "k1dpnde9", True),
    ("Aguiar", "k1dpc1bl", False),
    ("Minas", "k1dp7f2n", True),
    ("Poofy", "k1dohvrc", True),
    ("Luno", "k1dofott", False),
    ("Luminin", "k1dofh5v", False),
    ("Lucas MEI", "k1doffcx", False),
    ("KIA", "k1dof8ph", False),
    ("Inova", "k1doe92i", True),
    ("Injox", "k1dodnll", False),
    ("Atv", "k1docw53", True),
    ("Vita", "k1do5vfx", True),
    ("JLAS", "k1dkfg0k", True),
    ("KFA", "k1dkfd6r", True),
    ("Mini", "k1dkf1le", True),
    ("Victor", "k1dkelj1", True),
    ("Barbosa", "k1dkeaxv", True),
    ("Oliveira", "k16yrimx", False),
]

# Default agenda: ON 18:00–22:00 BRT (start inclusive, end exclusive → the
# reconciler keeps the shop resumed 18:00–21:59 and pauses it at 22:00) every
# day of the week. day_of_week: 0=Mon … 6=Sun.
_DEFAULT_START_HOUR = 18
_DEFAULT_END_HOUR = 22
_PLATFORM = "shopee"
_DEPARTMENT = "geral"


async def main(*, apply: bool) -> None:
    async for s in get_session():
        existing = {
            a.name: a
            for a in (
                await s.execute(
                    select(MarketingAccount).where(
                        MarketingAccount.platform == _PLATFORM,
                        MarketingAccount.department == _DEPARTMENT,
                    )
                )
            ).scalars().all()
        }

        created = updated = windows_added = 0
        for name, adspower_user_id, schedule_enabled in _SHOPEE_ACCOUNTS:
            acc = existing.get(name)
            if acc is None:
                acc = MarketingAccount(
                    name=name,
                    platform=_PLATFORM,
                    department=_DEPARTMENT,
                    integration_id=None,
                    agent_enabled=False,
                    schedule_enabled=schedule_enabled,
                    adspower_user_id=adspower_user_id,
                )
                s.add(acc)
                await s.flush()  # need acc.id for the windows below
                created += 1
                print(f"CREATE {name:12s} adspower={adspower_user_id} "
                      f"schedule_enabled={schedule_enabled}")
            else:
                if acc.adspower_user_id != adspower_user_id:
                    print(f"UPDATE {name:12s} adspower "
                          f"{acc.adspower_user_id!r} -> {adspower_user_id!r}")
                    acc.adspower_user_id = adspower_user_id
                    updated += 1
                else:
                    print(f"OK     {name:12s} (unchanged)")

            # Default windows only when the shop has none — never overwrite
            # windows the operator tuned in the UI.
            has_windows = (
                await s.execute(
                    select(MarketingSchedule.id)
                    .where(MarketingSchedule.account_id == acc.id)
                    .limit(1)
                )
            ).first() is not None
            if not has_windows:
                for dow in range(7):
                    s.add(
                        MarketingSchedule(
                            account_id=acc.id,
                            day_of_week=dow,
                            start_hour=_DEFAULT_START_HOUR,
                            end_hour=_DEFAULT_END_HOUR,
                        )
                    )
                windows_added += 7
                print(f"       {name:12s} + window {_DEFAULT_START_HOUR:02d}:00-"
                      f"{_DEFAULT_END_HOUR:02d}:00 all days")

        summary = (
            f"accounts: created={created} updated={updated} "
            f"unchanged={len(_SHOPEE_ACCOUNTS) - created - updated} | "
            f"schedule rows added={windows_added}"
        )
        if apply:
            await s.commit()
            print(f"\nAPPLIED — {summary}")
        else:
            await s.rollback()
            print(f"\nDRY-RUN (nothing written) — {summary}")
            print("Re-run with --apply to persist.")
        break


if __name__ == "__main__":
    import asyncio

    _apply = "--apply" in sys.argv[1:]
    asyncio.run(main(apply=_apply))
