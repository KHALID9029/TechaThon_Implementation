"""Tests for backend/sim/clock.py -- the virtual clock that powers demo time-scaling.

Virtual-mode tests monkeypatch time.monotonic() directly so they're instant and
deterministic -- no real sleeping, no flakiness from actual elapsed wall time.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from backend.sim.clock import SimClock

DHAKA = ZoneInfo("Asia/Dhaka")


def test_real_mode_returns_current_time_in_configured_tz():
    clock = SimClock(tz_name="Asia/Dhaka", start_time=None, scale=1.0)
    before = datetime.now(DHAKA)
    result = clock.now()
    after = datetime.now(DHAKA)
    assert before <= result <= after
    assert result.utcoffset() == timedelta(hours=6)


def test_real_mode_ignores_scale():
    clock = SimClock(tz_name="Asia/Dhaka", start_time=None, scale=60.0)
    assert abs((datetime.now(DHAKA) - clock.now()).total_seconds()) < 1


def test_virtual_mode_starts_exactly_at_start_time(monkeypatch):
    anchor = [1000.0]
    monkeypatch.setattr("backend.sim.clock.time.monotonic", lambda: anchor[0])

    start = datetime(2026, 7, 3, 20, 45, 0, tzinfo=DHAKA)
    clock = SimClock(tz_name="Asia/Dhaka", start_time=start, scale=60.0)

    assert clock.now() == start


def test_virtual_mode_advances_scaled(monkeypatch):
    anchor = [1000.0]
    monkeypatch.setattr("backend.sim.clock.time.monotonic", lambda: anchor[0])

    start = datetime(2026, 7, 3, 20, 45, 0, tzinfo=DHAKA)
    clock = SimClock(tz_name="Asia/Dhaka", start_time=start, scale=60.0)

    anchor[0] = 1000.0 + 2  # 2 real seconds elapse
    # 60x speed -> 2 real seconds = 2 virtual minutes.
    assert clock.now() == start + timedelta(minutes=2)


def test_virtual_mode_crosses_after_hours_quickly(monkeypatch):
    """This is the exact demo scenario from docs/API_CONTRACT.md §2 / PROJECT_PLAN.md §5:
    SIM_START_TIME=...20:45 + SIM_TIME_SCALE=60 should cross 17:00 (already past, so it's
    already after-hours) and reach a 2h-later point within ~2 real minutes."""
    anchor = [0.0]
    monkeypatch.setattr("backend.sim.clock.time.monotonic", lambda: anchor[0])

    start = datetime(2026, 7, 3, 20, 45, 0, tzinfo=DHAKA)
    clock = SimClock(tz_name="Asia/Dhaka", start_time=start, scale=60.0)

    anchor[0] = 120.0  # 2 real minutes elapse
    result = clock.now()
    assert result == start + timedelta(hours=2)
    assert not (9 <= result.hour < 17)  # outside office hours, as expected


def test_virtual_mode_naive_start_time_gets_tz_attached():
    clock = SimClock(tz_name="Asia/Dhaka", start_time=datetime(2026, 7, 3, 20, 45, 0), scale=1.0)
    assert clock.start_time is not None
    assert clock.start_time.tzinfo is not None
    assert clock.now().utcoffset() == timedelta(hours=6)


def test_now_iso_matches_contract_format(monkeypatch):
    anchor = [1000.0]
    monkeypatch.setattr("backend.sim.clock.time.monotonic", lambda: anchor[0])
    start = datetime(2026, 7, 3, 20, 45, 0, tzinfo=DHAKA)
    clock = SimClock(tz_name="Asia/Dhaka", start_time=start, scale=1.0)

    iso = clock.now_iso()
    assert iso.startswith("2026-07-03T20:45:00")
    assert iso.endswith("+06:00")


def test_today_returns_iso_date(monkeypatch):
    anchor = [1000.0]
    monkeypatch.setattr("backend.sim.clock.time.monotonic", lambda: anchor[0])
    start = datetime(2026, 7, 3, 23, 59, 0, tzinfo=DHAKA)
    clock = SimClock(tz_name="Asia/Dhaka", start_time=start, scale=1.0)

    assert clock.today() == "2026-07-03"


def test_module_level_now_uses_the_singleton():
    from backend.sim import clock as clock_module

    assert isinstance(clock_module.clock, SimClock)
    # default test env has no SIM_START_TIME -> real mode, wrapper matches real time
    result = clock_module.now()
    assert abs((datetime.now(DHAKA) - result).total_seconds()) < 2
