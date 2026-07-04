"""Round-trip tests for backend/state/store.py (Phase 2 "Done means..." checklist).

Every test gets its own temp SQLite file via the tmp_path fixture, so tests
never touch the real office.db and never interfere with each other.
"""
from pathlib import Path

import aiosqlite
import pytest

from backend.state import store


@pytest.fixture
async def db_path(tmp_path: Path) -> str:
    path = str(tmp_path / "test_office.db")
    await store.init_db(db_path=path)
    return path


async def test_init_db_creates_all_five_tables(db_path: str):
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ) as cursor:
            rows = await cursor.fetchall()
    tables = {row[0] for row in rows}
    assert {"room", "device", "room_state", "alert", "daily_usage"} <= tables


async def test_seed_if_empty_inserts_3_rooms_and_15_devices(db_path: str):
    seeded = await store.seed_if_empty(db_path=db_path)
    assert seeded is True

    snapshot = await store.get_snapshot(db_path=db_path)
    assert len(snapshot.rooms) == 3
    assert len(snapshot.devices) == 15

    for room_snapshot in snapshot.rooms:
        fans = [d for d in room_snapshot.devices if d.kind == "fan"]
        lights = [d for d in room_snapshot.devices if d.kind == "light"]
        assert len(fans) == 2
        assert len(lights) == 3
        assert all(d.state == "off" for d in room_snapshot.devices)


async def test_seed_if_empty_is_idempotent(db_path: str):
    first = await store.seed_if_empty(db_path=db_path)
    second = await store.seed_if_empty(db_path=db_path)
    assert first is True
    assert second is False

    snapshot = await store.get_snapshot(db_path=db_path)
    assert len(snapshot.devices) == 15  # not doubled by the second call


async def test_update_device_state_round_trips(db_path: str):
    await store.seed_if_empty(db_path=db_path)
    before = await store.get_snapshot(db_path=db_path)
    device = before.devices[0]
    assert device.state == "off"

    await store.update_device_state(
        device.id, "on", ts="2026-07-04T09:00:00+00:00", db_path=db_path
    )

    after = await store.get_snapshot(db_path=db_path)
    updated = next(d for d in after.devices if d.id == device.id)
    assert updated.state == "on"
    assert updated.last_changed == "2026-07-04T09:00:00+00:00"


async def test_snapshot_watts_total_reflects_on_devices(db_path: str):
    await store.seed_if_empty(db_path=db_path)
    snapshot = await store.get_snapshot(db_path=db_path)
    drawing_fan = next(d for d in snapshot.devices if d.id == "drawing.fan.1")
    drawing_light = next(d for d in snapshot.devices if d.id == "drawing.light.1")

    await store.update_device_state(drawing_fan.id, "on", db_path=db_path)
    await store.update_device_state(drawing_light.id, "on", db_path=db_path)

    snapshot = await store.get_snapshot(db_path=db_path)
    drawing_room = next(rs for rs in snapshot.rooms if rs.room.id == "drawing")
    assert drawing_room.watts_now == 60 + 15
    assert snapshot.watts_total == 75


async def test_room_state_all_on_since_round_trips(db_path: str):
    await store.seed_if_empty(db_path=db_path)

    state = await store.get_room_state("drawing", db_path=db_path)
    assert state is not None
    assert state.all_on_since is None

    await store.set_room_all_on_since("drawing", "2026-07-04T09:00:00+00:00", db_path=db_path)
    state = await store.get_room_state("drawing", db_path=db_path)
    assert state.all_on_since == "2026-07-04T09:00:00+00:00"

    await store.set_room_all_on_since("drawing", None, db_path=db_path)
    state = await store.get_room_state("drawing", db_path=db_path)
    assert state.all_on_since is None


async def test_room_state_debounce_columns_round_trip(db_path: str):
    await store.seed_if_empty(db_path=db_path)

    await store.set_room_last_alert_at("work2", "all_on_2h", "2026-07-04T22:00:00+00:00", db_path=db_path)
    await store.set_room_last_alert_at("work2", "after_hours", "2026-07-04T22:05:00+00:00", db_path=db_path)

    state = await store.get_room_state("work2", db_path=db_path)
    assert state.last_alert_at == "2026-07-04T22:00:00+00:00"
    assert state.last_hours_alert == "2026-07-04T22:05:00+00:00"


async def test_alert_insert_and_get(db_path: str):
    await store.seed_if_empty(db_path=db_path)

    alert_id = await store.insert_alert(
        room_id="work2",
        device_id=None,
        kind="after_hours",
        message="Work Room 2 still has devices on after hours.",
        ts="2026-07-04T22:00:00+00:00",
        db_path=db_path,
    )
    assert alert_id > 0

    alerts = await store.get_alerts(limit=10, db_path=db_path)
    assert len(alerts) == 1
    assert alerts[0].kind == "after_hours"
    assert alerts[0].room_id == "work2"
    assert alerts[0].acked is False


async def test_get_alerts_respects_limit_and_order(db_path: str):
    await store.seed_if_empty(db_path=db_path)

    for i in range(3):
        await store.insert_alert(
            room_id="work1",
            device_id=None,
            kind="after_hours",
            message=f"alert {i}",
            ts=f"2026-07-04T2{i}:00:00+00:00",
            db_path=db_path,
        )

    alerts = await store.get_alerts(limit=2, db_path=db_path)
    assert len(alerts) == 2
    # newest first
    assert alerts[0].message == "alert 2"


async def test_daily_usage_accumulates(db_path: str):
    await store.add_daily_usage(300.0, day="2026-07-04", db_path=db_path)
    await store.add_daily_usage(150.0, day="2026-07-04", db_path=db_path)

    total = await store.get_daily_usage(day="2026-07-04", db_path=db_path)
    assert total == 450.0


async def test_get_daily_usage_defaults_to_zero_when_absent(db_path: str):
    total = await store.get_daily_usage(day="2099-01-01", db_path=db_path)
    assert total == 0.0
