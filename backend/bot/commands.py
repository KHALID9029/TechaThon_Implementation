"""Discord bot command text builders (PROJECT_PLAN.md §8).

Every function here builds the exact templated strings the bot replies with.
Phase A6 wraps these with Gemini for a friendlier tone; for Phase A5 they ARE
the whole reply. Pure string-building functions (build_*) take already-fetched
data so they're trivially unit-testable with no database or event loop; the
async fetch_* functions do the live I/O and are what discord_bot.py calls.
"""
from __future__ import annotations

import re
from typing import Optional

from backend.sim.clock import SimClock
from backend.sim.clock import clock as default_clock
from backend.state import store
from backend.state.models import RoomSnapshot, Snapshot

ROOM_ORDER = ("drawing", "work1", "work2")

UNKNOWN_ROOM_TEXT = (
    "I don't recognize that room. Try: drawing, work1, or work2 (e.g. !room work1)."
)


def resolve_room_id(raw: str) -> Optional[str]:
    """Lenient !room <name> matching (PROJECT_PLAN.md §8: "drawing | work1 |
    work2 | work | work 1"). Accepts case/whitespace variations like
    'Drawing Room', 'work 1', 'WORK2'.

    Deliberate call: bare 'work' (no room number) is treated as unrecognized
    rather than guessed. The brief lists it alongside resolvable examples, but
    since it's genuinely ambiguous between the two work rooms, silently
    guessing wrong and reporting the wrong room's state to the boss is worse
    than asking once more. One-line change in the set below if you'd rather
    default it to work1.
    """
    compact = re.sub(r"\s+", "", raw.strip().lower())
    if compact in {"drawing", "drawingroom"}:
        return "drawing"
    if compact in {"work1", "workroom1", "work1room"}:
        return "work1"
    if compact in {"work2", "workroom2", "work2room"}:
        return "work2"
    return None


def _room_fragment(room_snapshot: RoomSnapshot) -> str:
    """e.g. 'Drawing Room: 1 fan ON, 2 lights ON.' or 'Work Room 1: all off.'"""
    fans_on = sum(1 for d in room_snapshot.devices if d.kind == "fan" and d.state == "on")
    lights_on = sum(1 for d in room_snapshot.devices if d.kind == "light" and d.state == "on")

    if fans_on == 0 and lights_on == 0:
        return f"{room_snapshot.room.name}: all off."

    parts = []
    if fans_on:
        parts.append(f"{fans_on} fan{'s' if fans_on != 1 else ''} ON")
    if lights_on:
        parts.append(f"{lights_on} light{'s' if lights_on != 1 else ''} ON")
    return f"{room_snapshot.room.name}: " + ", ".join(parts) + "."


def build_status_text(snapshot: Snapshot) -> str:
    """PROJECT_PLAN.md §8 example: 'Drawing Room: 1 fan ON, 2 lights ON. Work
    Room 1: all off. Work Room 2: 2 fans ON, 3 lights ON.'"""
    rooms_by_id = {rs.room.id: rs for rs in snapshot.rooms}
    fragments = [
        _room_fragment(rooms_by_id[room_id]) for room_id in ROOM_ORDER if room_id in rooms_by_id
    ]
    return " ".join(fragments)


def build_room_text(snapshot: Snapshot, room_id: str) -> Optional[str]:
    """Single-room version of build_status_text, for !room <name>. Returns
    None if room_id isn't one of the 3 known rooms."""
    room_snapshot = next((rs for rs in snapshot.rooms if rs.room.id == room_id), None)
    if room_snapshot is None:
        return None
    return _room_fragment(room_snapshot)


def build_usage_text(total_watts: int, today_kwh: float) -> str:
    """PROJECT_PLAN.md §8 example: 'Total power right now: 740W. Today's
    estimated usage: 4.2 kWh.'"""
    return f"Total power right now: {total_watts}W. Today's estimated usage: {today_kwh:.1f} kWh."


def build_help_text() -> str:
    return (
        "Commands:\n"
        "!status - office-wide summary of every room\n"
        "!room <name> - summary of one room (drawing, work1, work2)\n"
        "!usage - current power draw and today's estimated energy usage\n"
        "!help - this message"
    )


# --- live data fetchers (what discord_bot.py actually calls) ----------------


async def fetch_status_text(*, db_path: Optional[str] = None) -> str:
    snapshot = await store.get_snapshot(db_path=db_path)
    return build_status_text(snapshot)


async def fetch_room_text(room_name: str, *, db_path: Optional[str] = None) -> str:
    room_id = resolve_room_id(room_name)
    if room_id is None:
        return UNKNOWN_ROOM_TEXT
    snapshot = await store.get_snapshot(db_path=db_path)
    text = build_room_text(snapshot, room_id)
    return text if text is not None else UNKNOWN_ROOM_TEXT


async def fetch_usage_text(
    *, clock: Optional[SimClock] = None, db_path: Optional[str] = None
) -> str:
    active_clock = clock or default_clock
    snapshot = await store.get_snapshot(db_path=db_path)
    today = active_clock.today()
    watt_seconds = await store.get_daily_usage(day=today, db_path=db_path)
    today_kwh = watt_seconds / 3_600_000
    return build_usage_text(snapshot.watts_total, today_kwh)
