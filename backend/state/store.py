"""Async SQLite state store.

Every DB access anywhere in this project -- API routes, simulator, alert
evaluator, Discord bot -- goes through this module. Nothing else is allowed to
open a sqlite3/aiosqlite connection directly. This is what keeps the dashboard
and the bot from ever drifting apart (PROJECT_PLAN.md §1, "single source of
truth").

Each function opens (and closes) its own short-lived connection rather than
sharing one across the app. At 15 devices on a 5s tick this is simple and fast
enough (PROJECT_PLAN.md §4.1) and avoids any cross-task connection contention.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Optional

import aiosqlite

from backend.config import config
from backend.state.models import Alert, Device, Room, RoomSnapshot, RoomState, Snapshot

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "db" / "schema.sql"

# Fixed office layout -- matches the brief's floor plan and PROJECT_PLAN.md §0/§4 exactly.
ROOMS: list[tuple[str, str, str]] = [
    ("drawing", "Drawing Room", "Waiting area where people occasionally sit."),
    ("work1", "Work Room 1", "Where employees work."),
    ("work2", "Work Room 2", "Where employees work."),
]

# 2 fans + 3 lights per room = 5 devices/room, 15 total. (kind, index, wattage)
DEVICE_TEMPLATE: list[tuple[str, int, int]] = [
    ("fan", 1, 60),
    ("fan", 2, 60),
    ("light", 1, 15),
    ("light", 2, 15),
    ("light", 3, 15),
]


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _today() -> str:
    return dt.datetime.now(dt.timezone.utc).date().isoformat()


def _device_id(room_id: str, kind: str, index: int) -> str:
    return f"{room_id}.{kind}.{index}"


def _device_name(room_name: str, kind: str, index: int) -> str:
    return f"{room_name} {kind.capitalize()} {index}"


async def init_db(db_path: Optional[str] = None) -> None:
    """Create the schema if it doesn't exist yet. Safe to call on every startup."""
    path = db_path or config.db_path
    schema_sql = SCHEMA_PATH.read_text()
    async with aiosqlite.connect(path) as db:
        await db.execute("PRAGMA foreign_keys = ON;")
        await db.executescript(schema_sql)
        await db.commit()


async def seed_if_empty(db_path: Optional[str] = None) -> bool:
    """Insert the 3 rooms + 15 devices if the device table is empty.

    Returns True if it seeded, False if devices already existed (no-op).
    """
    path = db_path or config.db_path
    async with aiosqlite.connect(path) as db:
        async with db.execute("SELECT COUNT(*) FROM device") as cursor:
            row = await cursor.fetchone()
            existing = row[0] if row else 0
        if existing > 0:
            return False

        ts = _now()

        await db.executemany(
            "INSERT INTO room (id, name, description) VALUES (?, ?, ?)",
            ROOMS,
        )
        await db.executemany(
            "INSERT INTO room_state (room_id, all_on_since, last_alert_at, last_hours_alert) "
            "VALUES (?, NULL, NULL, NULL)",
            [(room_id,) for room_id, _name, _desc in ROOMS],
        )

        device_rows = []
        for room_id, room_name, _desc in ROOMS:
            for kind, index, wattage in DEVICE_TEMPLATE:
                device_rows.append(
                    (
                        _device_id(room_id, kind, index),
                        room_id,
                        kind,
                        index,
                        _device_name(room_name, kind, index),
                        "off",
                        wattage,
                        ts,
                    )
                )
        await db.executemany(
            "INSERT INTO device "
            "(id, room_id, kind, device_index, name, state, wattage, last_changed) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            device_rows,
        )
        await db.commit()
        return True


def _row_to_device(row: aiosqlite.Row) -> Device:
    return Device(
        id=row["id"],
        room_id=row["room_id"],
        kind=row["kind"],
        device_index=row["device_index"],
        name=row["name"],
        state=row["state"],
        wattage=row["wattage"],
        last_changed=row["last_changed"],
    )


async def get_snapshot(db_path: Optional[str] = None) -> Snapshot:
    """Full state for GET /api/state and for the bot's !status / !room / !usage commands."""
    path = db_path or config.db_path
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM room ORDER BY id") as cursor:
            room_rows = await cursor.fetchall()
        async with db.execute(
            "SELECT * FROM device ORDER BY room_id, kind, device_index"
        ) as cursor:
            device_rows = await cursor.fetchall()

    devices = [_row_to_device(r) for r in device_rows]
    rooms = [Room(id=r["id"], name=r["name"], description=r["description"]) for r in room_rows]

    room_snapshots: list[RoomSnapshot] = []
    for room in rooms:
        room_devices = [d for d in devices if d.room_id == room.id]
        watts_now = sum(d.wattage for d in room_devices if d.state == "on")
        room_snapshots.append(RoomSnapshot(room=room, devices=room_devices, watts_now=watts_now))

    watts_total = sum(rs.watts_now for rs in room_snapshots)

    return Snapshot(
        rooms=room_snapshots,
        devices=devices,
        watts_total=watts_total,
        generated_at=_now(),
    )


async def update_device_state(
    device_id: str,
    state: str,
    ts: Optional[str] = None,
    db_path: Optional[str] = None,
) -> None:
    """Flip a device's on/off state. Called by the simulator on every toggle."""
    path = db_path or config.db_path
    ts = ts or _now()
    async with aiosqlite.connect(path) as db:
        await db.execute(
            "UPDATE device SET state = ?, last_changed = ? WHERE id = ?",
            (state, ts, device_id),
        )
        await db.commit()


async def get_room_state(room_id: str, db_path: Optional[str] = None) -> Optional[RoomState]:
    path = db_path or config.db_path
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM room_state WHERE room_id = ?", (room_id,)
        ) as cursor:
            row = await cursor.fetchone()
    if row is None:
        return None
    return RoomState(
        room_id=row["room_id"],
        all_on_since=row["all_on_since"],
        last_alert_at=row["last_alert_at"],
        last_hours_alert=row["last_hours_alert"],
    )


async def set_room_all_on_since(
    room_id: str, ts_or_null: Optional[str], db_path: Optional[str] = None
) -> None:
    """Set/clear room_state.all_on_since -- see PROJECT_PLAN.md §4.2 for the transition rules."""
    path = db_path or config.db_path
    async with aiosqlite.connect(path) as db:
        await db.execute(
            "UPDATE room_state SET all_on_since = ? WHERE room_id = ?",
            (ts_or_null, room_id),
        )
        await db.commit()


async def set_room_last_alert_at(
    room_id: str, kind: str, ts: str, db_path: Optional[str] = None
) -> None:
    """Update the debounce timestamp for a room's alert kind.

    kind='all_on_2h'   -> room_state.last_alert_at
    kind='after_hours' -> room_state.last_hours_alert
    """
    column = "last_alert_at" if kind == "all_on_2h" else "last_hours_alert"
    path = db_path or config.db_path
    async with aiosqlite.connect(path) as db:
        await db.execute(
            f"UPDATE room_state SET {column} = ? WHERE room_id = ?",  # noqa: S608 (column is one of two fixed literals above)
            (ts, room_id),
        )
        await db.commit()


async def get_alerts(limit: int = 50, db_path: Optional[str] = None) -> list[Alert]:
    path = db_path or config.db_path
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM alert ORDER BY created_at DESC LIMIT ?", (limit,)
        ) as cursor:
            rows = await cursor.fetchall()
    return [
        Alert(
            id=row["id"],
            room_id=row["room_id"],
            device_id=row["device_id"],
            kind=row["kind"],
            message=row["message"],
            created_at=row["created_at"],
            acked=bool(row["acked"]),
        )
        for row in rows
    ]


async def insert_alert(
    room_id: Optional[str],
    device_id: Optional[str],
    kind: str,
    message: str,
    ts: Optional[str] = None,
    db_path: Optional[str] = None,
) -> int:
    path = db_path or config.db_path
    ts = ts or _now()
    async with aiosqlite.connect(path) as db:
        cursor = await db.execute(
            "INSERT INTO alert (room_id, device_id, kind, message, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (room_id, device_id, kind, message, ts),
        )
        await db.commit()
        return cursor.lastrowid  # type: ignore[return-value]


async def add_daily_usage(
    watt_seconds: float, day: Optional[str] = None, db_path: Optional[str] = None
) -> None:
    """Integrate power draw into today's running total (called every sim tick)."""
    path = db_path or config.db_path
    day = day or _today()
    async with aiosqlite.connect(path) as db:
        await db.execute(
            "INSERT INTO daily_usage (day, watt_seconds) VALUES (?, ?) "
            "ON CONFLICT(day) DO UPDATE SET watt_seconds = watt_seconds + excluded.watt_seconds",
            (day, watt_seconds),
        )
        await db.commit()


async def get_daily_usage(day: Optional[str] = None, db_path: Optional[str] = None) -> float:
    """Returns today's accumulated watt-seconds (0.0 if nothing recorded yet)."""
    path = db_path or config.db_path
    day = day or _today()
    async with aiosqlite.connect(path) as db:
        async with db.execute(
            "SELECT watt_seconds FROM daily_usage WHERE day = ?", (day,)
        ) as cursor:
            row = await cursor.fetchone()
    return row[0] if row else 0.0
