"""desired_state — BRT window math + manual override.

Pure unit tests: MarketingAccount/MarketingSchedule are built in memory
(never flushed), so no DB is touched. The whole point is that the windows
are read in America/Sao_Paulo, NOT UTC — BRT = UTC-3 (no DST since 2019),
so a BRT noon is 15:00 UTC.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.models.marketing import MarketingAccount, MarketingSchedule
from app.services.marketing.scheduling import desired_state, next_transition


def _acc(**kw) -> MarketingAccount:
    return MarketingAccount(
        name="t", platform="shopee", department="geral", acos_target=7.0,
        override_action=kw.get("override_action"),
        override_until=kw.get("override_until"),
    )


# 2026-06-15 is a Monday (weekday 0).
def _mon_brt_noon_utc() -> datetime:
    # BRT 12:00 Mon == 15:00 UTC.
    return datetime(2026, 6, 15, 15, 0, tzinfo=UTC)


def _sched(dow: int, start: int, end: int) -> MarketingSchedule:
    return MarketingSchedule(account_id=None, day_of_week=dow, start_hour=start, end_hour=end)


def test_inside_window_is_on():
    # Mon 11–13 BRT; now = Mon 12:00 BRT → on.
    acc = _acc()
    schedules = [_sched(0, 11, 13)]
    assert desired_state(acc, schedules, _mon_brt_noon_utc()) == "on"


def test_outside_window_is_off():
    # Mon 12:00 BRT but window only 18–22 → off.
    acc = _acc()
    schedules = [_sched(0, 18, 22)]
    assert desired_state(acc, schedules, _mon_brt_noon_utc()) == "off"


def test_utc_naive_would_be_wrong_but_brt_is_right():
    # 23:00 UTC on Mon = 20:00 BRT (still Monday). Window Mon 18–22 → on.
    # If the code used UTC hour (23) it'd be OFF — this guards the bug.
    acc = _acc()
    schedules = [_sched(0, 18, 22)]
    now = datetime(2026, 6, 15, 23, 0, tzinfo=UTC)
    assert desired_state(acc, schedules, now) == "on"


def test_midnight_wrap_window():
    # Window Mon 22→02 (end<=start, wraps). BRT 23:00 Mon → on.
    acc = _acc()
    schedules = [_sched(0, 22, 2)]
    now = datetime(2026, 6, 16, 2, 0, tzinfo=UTC)  # 23:00 BRT Mon
    assert desired_state(acc, schedules, now) == "on"
    # BRT 12:00 Mon → off (outside the late-night block).
    assert desired_state(acc, schedules, _mon_brt_noon_utc()) == "off"


def test_override_pause_forces_off_inside_window():
    acc = _acc(override_action="pause")
    schedules = [_sched(0, 11, 13)]  # would be ON
    assert desired_state(acc, schedules, _mon_brt_noon_utc()) == "off"


def test_override_resume_forces_on_outside_window():
    acc = _acc(override_action="resume")
    schedules = [_sched(0, 18, 22)]  # would be OFF at noon
    assert desired_state(acc, schedules, _mon_brt_noon_utc()) == "on"


def test_override_expired_falls_back_to_window():
    now = _mon_brt_noon_utc()
    # Override said pause but expired an hour ago → windows decide (ON).
    acc = _acc(override_action="pause", override_until=now - timedelta(hours=1))
    schedules = [_sched(0, 11, 13)]
    assert desired_state(acc, schedules, now) == "on"


def test_override_future_still_active():
    now = _mon_brt_noon_utc()
    acc = _acc(override_action="pause", override_until=now + timedelta(hours=1))
    schedules = [_sched(0, 11, 13)]
    assert desired_state(acc, schedules, now) == "off"


def test_next_transition_finds_window_close():
    # Window Mon 11–13 BRT; at 12:00 the next flip (→off) is 13:00 BRT = 16:00 UTC.
    acc = _acc()
    schedules = [_sched(0, 11, 13)]
    nxt = next_transition(acc, schedules, _mon_brt_noon_utc())
    assert nxt is not None
    assert nxt.astimezone(UTC).hour == 16  # 13:00 BRT
