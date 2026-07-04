"""Translates internal store/model shapes (backend/state/models.py, matched to
backend/db/schema.sql) into the exact wire shapes frozen in
docs/API_CONTRACT.md. This is the one place that boundary crossing happens --
routes and the WS layer should build responses through these functions, never
by hand, so there is exactly one place that can drift from the contract.
"""
from __future__ import annotations

from backend.state.models import Alert, Device, RoomSnapshot, Snapshot

# Office hours are fixed by the brief (9 AM-5 PM), not currently configurable.
# Phase A4's alert evaluator should import these same constants rather than
# hardcoding its own copy, so the two can never drift apart.
OFFICE_HOURS_START = "09:00"
OFFICE_HOURS_END = "17:00"


def device_to_api(device: Device) -> dict:
    """docs/API_CONTRACT.md §4 Device shape: no room_id (implied by nesting
    under a room), and `index` not `device_index` (handled by the model's
    alias -- see backend/state/models.py)."""
    return device.model_dump(by_alias=True, exclude={"room_id"})


def room_snapshot_to_api(room_snapshot: RoomSnapshot) -> dict:
    """docs/API_CONTRACT.md §4 Room shape (inside rooms[])."""
    return {
        "id": room_snapshot.room.id,
        "name": room_snapshot.room.name,
        "watts": room_snapshot.watts_now,
        "devices": [device_to_api(d) for d in room_snapshot.devices],
    }


def snapshot_to_api(snapshot: Snapshot, *, server_time: str, today_kwh: float) -> dict:
    """docs/API_CONTRACT.md §4 Snapshot shape -- returned by GET /api/state and
    the `snapshot` WS frame. `server_time` and `today_kwh` aren't part of the
    internal Snapshot model (they come from the virtual clock and the
    daily_usage table respectively), so callers pass them in explicitly."""
    return {
        "server_time": server_time,
        "office_hours": {"start": OFFICE_HOURS_START, "end": OFFICE_HOURS_END},
        "total_watts": snapshot.watts_total,
        "today_kwh": today_kwh,
        "rooms": [room_snapshot_to_api(rs) for rs in snapshot.rooms],
    }


def alert_to_api(alert: Alert) -> dict:
    """docs/API_CONTRACT.md §4 Alert shape -- field names already match
    exactly (id, room_id, device_id, kind, message, created_at, acked), so no
    alias/exclude massaging is needed here, unlike Device."""
    return alert.model_dump()
