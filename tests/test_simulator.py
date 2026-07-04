"""Tests for backend/sim/simulator.py + backend/sim/prelude.py (Phase A2's
"fixed seed -> deterministic" requirement, plus the prelude beats and the
bus event shapes the API layer will forward almost as-is in Phase A3).
"""
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from backend.sim.clock import SimClock
from backend.sim.simulator import Simulator, probability_for
from backend.state import store
from backend.state.bus import Bus

DHAKA = ZoneInfo("Asia/Dhaka")


@pytest.fixture
async def db_path(tmp_path: Path) -> str:
    path = str(tmp_path / "sim_test.db")
    await store.init_db(db_path=path)
    await store.seed_if_empty(db_path=path)
    return path


def make_simulator(
    db_path: str,
    *,
    seed: int = 1,
    start_time: datetime | None = None,
    scale: float = 1.0,
    tick_ms: int = 5000,
):
    clock = SimClock(tz_name="Asia/Dhaka", start_time=start_time, scale=scale)
    bus = Bus()
    sim = Simulator(tick_ms=tick_ms, seed=seed, clock=clock, bus_=bus, db_path=db_path)
    return sim, clock, bus


# --- probability table ----------------------------------------------------


def test_probability_for_covers_every_hour_of_day():
    for hour in range(24):
        assert 0.0 <= probability_for("fan", hour) <= 1.0
        assert 0.0 <= probability_for("light", hour) <= 1.0


def test_probability_for_matches_project_plan_table():
    assert probability_for("fan", 3) == 0.02
    assert probability_for("light", 3) == 0.01
    assert probability_for("fan", 7) == 0.10
    assert probability_for("light", 7) == 0.05
    assert probability_for("fan", 10) == 0.30
    assert probability_for("light", 10) == 0.40
    assert probability_for("fan", 18) == 0.18
    assert probability_for("light", 18) == 0.22
    assert probability_for("fan", 21) == 0.10
    assert probability_for("light", 21) == 0.15
    assert probability_for("fan", 23) == 0.05
    assert probability_for("light", 23) == 0.08


def test_probability_for_invalid_hour_raises():
    with pytest.raises(ValueError):
        probability_for("fan", 24)


# --- determinism -----------------------------------------------------------


async def test_same_seed_produces_identical_toggle_sequence(tmp_path):
    path_a = str(tmp_path / "a.db")
    path_b = str(tmp_path / "b.db")
    await store.init_db(db_path=path_a)
    await store.seed_if_empty(db_path=path_a)
    await store.init_db(db_path=path_b)
    await store.seed_if_empty(db_path=path_b)

    # A neutral daytime start with no prelude checkpoint in range, so only the
    # RNG (not scripted events) drives the toggles for this comparison.
    start = datetime(2026, 7, 3, 10, 0, 0, tzinfo=DHAKA)
    sim_a, _, _ = make_simulator(path_a, seed=99, start_time=start, scale=1.0)
    sim_b, _, _ = make_simulator(path_b, seed=99, start_time=start, scale=1.0)

    for _ in range(15):
        await sim_a.tick()
    for _ in range(15):
        await sim_b.tick()

    snap_a = await store.get_snapshot(db_path=path_a)
    snap_b = await store.get_snapshot(db_path=path_b)
    assert [(d.id, d.state) for d in snap_a.devices] == [(d.id, d.state) for d in snap_b.devices]


async def test_different_seed_can_produce_different_sequence(tmp_path):
    """Not a strict guarantee (two seeds could coincidentally agree), but with
    15 ticks over 15 devices at office-hours probabilities, agreement by chance
    is astronomically unlikely -- this catches an accidental 'seed does nothing' bug."""
    path_a = str(tmp_path / "a.db")
    path_b = str(tmp_path / "b.db")
    await store.init_db(db_path=path_a)
    await store.seed_if_empty(db_path=path_a)
    await store.init_db(db_path=path_b)
    await store.seed_if_empty(db_path=path_b)

    start = datetime(2026, 7, 3, 10, 0, 0, tzinfo=DHAKA)
    sim_a, _, _ = make_simulator(path_a, seed=1, start_time=start, scale=1.0)
    sim_b, _, _ = make_simulator(path_b, seed=2, start_time=start, scale=1.0)

    for _ in range(15):
        await sim_a.tick()
    for _ in range(15):
        await sim_b.tick()

    snap_a = await store.get_snapshot(db_path=path_a)
    snap_b = await store.get_snapshot(db_path=path_b)
    assert [(d.id, d.state) for d in snap_a.devices] != [(d.id, d.state) for d in snap_b.devices]


# --- prelude -----------------------------------------------------------------


async def test_prelude_0900_turns_work_room_1_all_on(db_path):
    start = datetime(2026, 7, 3, 9, 0, 1, tzinfo=DHAKA)
    sim, _, _ = make_simulator(db_path, start_time=start, scale=1.0)

    summary = await sim.tick()
    assert any("Work Room 1" in d for d in summary["fired_prelude_events"])

    snapshot = await store.get_snapshot(db_path=db_path)
    work1 = next(rs for rs in snapshot.rooms if rs.room.id == "work1")
    assert all(d.state == "on" for d in work1.devices)


async def test_prelude_event_fires_only_once(db_path):
    start = datetime(2026, 7, 3, 9, 0, 1, tzinfo=DHAKA)
    sim, _, _ = make_simulator(db_path, start_time=start, scale=1.0)

    first = await sim.tick()
    second = await sim.tick()

    assert any("Work Room 1" in d for d in first["fired_prelude_events"])
    assert not any("Work Room 1" in d for d in second["fired_prelude_events"])


async def test_prelude_2045_forces_work_room_2_all_on_after_hours(db_path):
    start = datetime(2026, 7, 3, 20, 45, 1, tzinfo=DHAKA)
    assert not (9 <= start.hour < 17)  # confirms this is indeed outside office hours

    sim, _, _ = make_simulator(db_path, start_time=start, scale=1.0)
    summary = await sim.tick()
    assert any("left on" in d for d in summary["fired_prelude_events"])

    snapshot = await store.get_snapshot(db_path=db_path)
    work2 = next(rs for rs in snapshot.rooms if rs.room.id == "work2")
    assert all(d.state == "on" for d in work2.devices)


async def test_prelude_forced_device_is_not_immediately_re_rolled(db_path, monkeypatch):
    """A device the prelude just forced on/off must not also be probability-rolled
    in that same tick -- otherwise the scripted beat could be invisibly undone."""
    start = datetime(2026, 7, 3, 9, 0, 1, tzinfo=DHAKA)
    sim, _, _ = make_simulator(db_path, start_time=start, scale=1.0)

    # Force every probability roll to "would toggle" (rng.random() always 0.0)
    # so the only thing preventing a second flip is the forced_ids skip-list.
    monkeypatch.setattr(sim._rng, "random", lambda: 0.0)

    await sim.tick()
    snapshot = await store.get_snapshot(db_path=db_path)
    work1 = next(rs for rs in snapshot.rooms if rs.room.id == "work1")
    assert all(d.state == "on" for d in work1.devices)


# --- energy integration ------------------------------------------------------


async def test_daily_usage_uses_virtual_scaled_seconds(db_path):
    start = datetime(2026, 7, 3, 9, 0, 0, tzinfo=DHAKA)
    sim, _, _ = make_simulator(db_path, start_time=start, scale=60.0, tick_ms=5000)

    summary = await sim.tick()
    today = start.date().isoformat()
    watt_seconds = await store.get_daily_usage(day=today, db_path=db_path)

    expected = summary["total_watts"] * (5000 / 1000) * 60.0
    assert watt_seconds == pytest.approx(expected)


# --- bus event shapes (must match docs/API_CONTRACT.md §5 exactly) ----------


async def test_device_change_and_usage_tick_shapes_match_contract(db_path):
    start = datetime(2026, 7, 3, 9, 0, 1, tzinfo=DHAKA)  # triggers the 09:00 prelude
    sim, _, bus = make_simulator(db_path, start_time=start, scale=1.0)
    queue = bus.subscribe()

    await sim.tick()

    events = []
    while not queue.empty():
        events.append(queue.get_nowait())

    device_changes = [e for e in events if e["type"] == "device_change"]
    assert len(device_changes) >= 5  # the 09:00 prelude alone forces 5 devices on

    sample = device_changes[0]
    assert set(sample["payload"].keys()) == {"device", "room_id", "room_watts", "total_watts"}
    assert set(sample["payload"]["device"].keys()) == {
        "id",
        "kind",
        "index",
        "name",
        "state",
        "wattage",
        "last_changed",
    }
    assert "room_id" not in sample["payload"]["device"]  # contract's Device has no room_id

    usage_ticks = [e for e in events if e["type"] == "usage_tick"]
    assert len(usage_ticks) == 1
    assert set(usage_ticks[0]["payload"].keys()) == {"total_watts", "today_kwh", "server_time"}
