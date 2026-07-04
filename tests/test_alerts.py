"""Tests for backend/alerts/evaluator.py (Phase A4): after_hours, all_on_2h,
debounce, the all_on_since transition, the startup bootstrap/'approx.' edge
case, and the alert_new bus event shape -- all against docs/API_CONTRACT.md
and PROJECT_PLAN.md §4.2.

Uses monkeypatched time.monotonic() (same pattern as test_clock.py /
test_simulator.py) so multi-hour and multi-minute scenarios run instantly.
"""
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from backend.alerts.evaluator import AlertEvaluator, within_office_hours
from backend.sim.clock import SimClock
from backend.state import store
from backend.state.bus import Bus

DHAKA = ZoneInfo("Asia/Dhaka")


@pytest.fixture
async def db_path(tmp_path: Path) -> str:
    path = str(tmp_path / "alerts_test.db")
    await store.init_db(db_path=path)
    await store.seed_if_empty(db_path=path)
    return path


def make_evaluator(db_path: str, *, start_time: datetime, scale: float = 1.0):
    clock = SimClock(tz_name="Asia/Dhaka", start_time=start_time, scale=scale)
    bus = Bus()
    evaluator = AlertEvaluator(clock=clock, bus_=bus, db_path=db_path)
    return evaluator, clock, bus


async def turn_on(db_path: str, room_id: str, ts: str) -> None:
    snapshot = await store.get_snapshot(db_path=db_path)
    for device in snapshot.devices:
        if device.room_id == room_id:
            await store.update_device_state(device.id, "on", ts=ts, db_path=db_path)


# --- within_office_hours ------------------------------------------------


def test_within_office_hours_boundaries():
    tz = DHAKA
    assert within_office_hours(datetime(2026, 7, 3, 9, 0, tzinfo=tz))
    assert within_office_hours(datetime(2026, 7, 3, 16, 59, tzinfo=tz))
    assert not within_office_hours(datetime(2026, 7, 3, 17, 0, tzinfo=tz))
    assert not within_office_hours(datetime(2026, 7, 3, 8, 59, tzinfo=tz))
    assert not within_office_hours(datetime(2026, 7, 3, 22, 0, tzinfo=tz))


# --- after_hours ---------------------------------------------------------


async def test_after_hours_fires_when_device_on_outside_office_hours(db_path):
    start = datetime(2026, 7, 3, 20, 45, 0, tzinfo=DHAKA)
    evaluator, clock, bus = make_evaluator(db_path, start_time=start)
    await turn_on(db_path, "work2", ts=start.isoformat())

    fired = await evaluator.tick()
    kinds = [a["kind"] for a in fired]
    assert "after_hours" in kinds

    alerts = await store.get_alerts(db_path=db_path)
    assert any(a.kind == "after_hours" and a.room_id == "work2" for a in alerts)


async def test_after_hours_does_not_fire_during_office_hours(db_path):
    start = datetime(2026, 7, 3, 11, 0, 0, tzinfo=DHAKA)
    evaluator, clock, bus = make_evaluator(db_path, start_time=start)
    await turn_on(db_path, "work2", ts=start.isoformat())

    fired = await evaluator.tick()
    assert "after_hours" not in [a["kind"] for a in fired]


async def test_after_hours_does_not_fire_with_no_devices_on(db_path):
    start = datetime(2026, 7, 3, 20, 45, 0, tzinfo=DHAKA)
    evaluator, clock, bus = make_evaluator(db_path, start_time=start)
    # nothing turned on -- fresh seed is all-off

    fired = await evaluator.tick()
    assert fired == []


async def test_after_hours_debounced_per_room(monkeypatch, db_path):
    start = datetime(2026, 7, 3, 20, 45, 0, tzinfo=DHAKA)
    evaluator, clock, bus = make_evaluator(db_path, start_time=start)
    await turn_on(db_path, "work2", ts=start.isoformat())

    first = await evaluator.tick()
    assert "after_hours" in [a["kind"] for a in first]

    # Immediately tick again (no time elapsed) -- must not refire.
    second = await evaluator.tick()
    assert "after_hours" not in [a["kind"] for a in second]

    alerts = await store.get_alerts(db_path=db_path)
    assert len([a for a in alerts if a.kind == "after_hours"]) == 1


async def test_after_hours_refires_after_debounce_window(monkeypatch, db_path):
    anchor = [0.0]
    monkeypatch.setattr("backend.sim.clock.time.monotonic", lambda: anchor[0])
    start = datetime(2026, 7, 3, 20, 45, 0, tzinfo=DHAKA)
    evaluator, clock, bus = make_evaluator(db_path, start_time=start, scale=1.0)
    await turn_on(db_path, "work2", ts=start.isoformat())

    await evaluator.tick()
    anchor[0] = 31 * 60  # 31 minutes later, past the 30-min debounce
    fired = await evaluator.tick()
    assert "after_hours" in [a["kind"] for a in fired]

    alerts = await store.get_alerts(db_path=db_path)
    assert len([a for a in alerts if a.kind == "after_hours"]) == 2


# --- all_on_since transition + all_on_2h ---------------------------------


async def test_all_on_since_is_set_when_room_becomes_fully_on(monkeypatch, db_path):
    anchor = [0.0]
    monkeypatch.setattr("backend.sim.clock.time.monotonic", lambda: anchor[0])
    start = datetime(2026, 7, 3, 9, 0, 0, tzinfo=DHAKA)
    evaluator, clock, bus = make_evaluator(db_path, start_time=start)
    await turn_on(db_path, "work1", ts=start.isoformat())

    await evaluator.tick()
    room_state = await store.get_room_state("work1", db_path=db_path)
    assert room_state.all_on_since == start.isoformat()


async def test_all_on_since_clears_when_room_stops_being_fully_on(db_path):
    start = datetime(2026, 7, 3, 9, 0, 0, tzinfo=DHAKA)
    evaluator, clock, bus = make_evaluator(db_path, start_time=start)
    await turn_on(db_path, "work1", ts=start.isoformat())
    await evaluator.tick()

    snapshot = await store.get_snapshot(db_path=db_path)
    one_device = next(d for d in snapshot.devices if d.room_id == "work1")
    await store.update_device_state(one_device.id, "off", ts=start.isoformat(), db_path=db_path)

    await evaluator.tick()
    room_state = await store.get_room_state("work1", db_path=db_path)
    assert room_state.all_on_since is None


async def test_all_on_2h_fires_after_two_virtual_hours(monkeypatch, db_path):
    anchor = [0.0]
    monkeypatch.setattr("backend.sim.clock.time.monotonic", lambda: anchor[0])
    start = datetime(2026, 7, 3, 20, 45, 0, tzinfo=DHAKA)
    evaluator, clock, bus = make_evaluator(db_path, start_time=start, scale=60.0)
    await turn_on(db_path, "work2", ts=start.isoformat())

    await evaluator.tick()  # establishes all_on_since
    anchor[0] = 121.0  # 121 real seconds * 60x = ~2h1m virtual
    fired = await evaluator.tick()

    assert "all_on_2h" in [a["kind"] for a in fired]
    alerts = await store.get_alerts(db_path=db_path)
    assert any(a.kind == "all_on_2h" and a.room_id == "work2" for a in alerts)


async def test_all_on_2h_does_not_fire_before_two_hours(monkeypatch, db_path):
    anchor = [0.0]
    monkeypatch.setattr("backend.sim.clock.time.monotonic", lambda: anchor[0])
    start = datetime(2026, 7, 3, 20, 45, 0, tzinfo=DHAKA)
    evaluator, clock, bus = make_evaluator(db_path, start_time=start, scale=60.0)
    await turn_on(db_path, "work2", ts=start.isoformat())

    await evaluator.tick()
    anchor[0] = 60.0  # only 1h virtual elapsed
    fired = await evaluator.tick()
    assert "all_on_2h" not in [a["kind"] for a in fired]


async def test_all_on_2h_resets_if_room_toggles_off_and_back_on(monkeypatch, db_path):
    """Edge case from PROJECT_PLAN.md §4.2: a device flips off and back on
    within the 2h window -- the timer must reset, not just continue."""
    anchor = [0.0]
    monkeypatch.setattr("backend.sim.clock.time.monotonic", lambda: anchor[0])
    start = datetime(2026, 7, 3, 20, 45, 0, tzinfo=DHAKA)
    evaluator, clock, bus = make_evaluator(db_path, start_time=start, scale=60.0)
    await turn_on(db_path, "work2", ts=start.isoformat())
    await evaluator.tick()

    anchor[0] = 60.0  # 1h virtual in, still under 2h
    snapshot = await store.get_snapshot(db_path=db_path)
    one_device = next(d for d in snapshot.devices if d.room_id == "work2")
    await store.update_device_state(one_device.id, "off", db_path=db_path)
    await evaluator.tick()  # room no longer fully on -> all_on_since clears

    await store.update_device_state(one_device.id, "on", db_path=db_path)
    anchor[0] = 121.0  # 2h1m virtual since t=0, but only ~1h since the reset
    fired = await evaluator.tick()
    assert "all_on_2h" not in [a["kind"] for a in fired]


async def test_all_on_2h_debounced(monkeypatch, db_path):
    anchor = [0.0]
    monkeypatch.setattr("backend.sim.clock.time.monotonic", lambda: anchor[0])
    start = datetime(2026, 7, 3, 20, 45, 0, tzinfo=DHAKA)
    evaluator, clock, bus = make_evaluator(db_path, start_time=start, scale=60.0)
    await turn_on(db_path, "work2", ts=start.isoformat())

    await evaluator.tick()
    anchor[0] = 121.0
    first = await evaluator.tick()
    assert "all_on_2h" in [a["kind"] for a in first]

    anchor[0] = 122.0  # 1 virtual minute later -- still within the 30-min debounce
    second = await evaluator.tick()
    assert "all_on_2h" not in [a["kind"] for a in second]


# --- bootstrap / approx reconciliation ------------------------------------


async def test_bootstrap_reconciles_already_all_on_room(db_path):
    """A room is already fully on the first time the evaluator ever looks at
    it (no persisted all_on_since) -- bootstrap should derive one from the
    devices' last_changed and mark it approx."""
    start = datetime(2026, 7, 3, 22, 0, 0, tzinfo=DHAKA)
    three_hours_ago = (start - timedelta(hours=3)).isoformat()
    await turn_on(db_path, "work2", ts=three_hours_ago)

    evaluator, clock, bus = make_evaluator(db_path, start_time=start, scale=1.0)
    await evaluator._bootstrap_all_on_since()

    room_state = await store.get_room_state("work2", db_path=db_path)
    assert room_state.all_on_since == three_hours_ago
    assert "work2" in evaluator._approx_rooms

    fired = await evaluator.tick()
    all_on_2h_alerts = [a for a in fired if a["kind"] == "all_on_2h"]
    assert len(all_on_2h_alerts) == 1
    assert "approx" in all_on_2h_alerts[0]["message"]


async def test_bootstrap_does_not_touch_room_with_existing_all_on_since(db_path):
    start = datetime(2026, 7, 3, 9, 0, 0, tzinfo=DHAKA)
    await turn_on(db_path, "work1", ts=start.isoformat())
    await store.set_room_all_on_since("work1", start.isoformat(), db_path=db_path)

    evaluator, clock, bus = make_evaluator(db_path, start_time=start, scale=1.0)
    await evaluator._bootstrap_all_on_since()

    assert "work1" not in evaluator._approx_rooms
    room_state = await store.get_room_state("work1", db_path=db_path)
    assert room_state.all_on_since == start.isoformat()


# --- bus event shape (must match docs/API_CONTRACT.md §5 exactly) -----------


async def test_alert_new_event_shape_matches_contract(db_path):
    start = datetime(2026, 7, 3, 20, 45, 0, tzinfo=DHAKA)
    evaluator, clock, bus = make_evaluator(db_path, start_time=start)
    await turn_on(db_path, "work2", ts=start.isoformat())
    queue = bus.subscribe()

    await evaluator.tick()

    events = []
    while not queue.empty():
        events.append(queue.get_nowait())

    alert_events = [e for e in events if e["type"] == "alert_new"]
    assert len(alert_events) == 1
    payload = alert_events[0]["payload"]
    assert set(payload.keys()) == {"alert"}
    assert set(payload["alert"].keys()) == {
        "id", "room_id", "device_id", "kind", "message", "created_at", "acked",
    }
    assert payload["alert"]["kind"] == "after_hours"
    assert payload["alert"]["acked"] is False
