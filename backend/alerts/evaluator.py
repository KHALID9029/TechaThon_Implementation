"""Full alert evaluator (PROJECT_PLAN.md §4.2, EXECUTION_PHASES.md Phase A4).

Runs on its own 30-real-second timer inside the FastAPI lifespan and checks
two conditions, per room, every tick:

- **after_hours**: any device in the room is `on` while the virtual clock is
  outside office hours (09:00-17:00). Debounced 30 (virtual) minutes per room.
- **all_on_2h**: every device in the room has been continuously `on` for at
  least 2 (virtual) hours. This can't be derived from any single device's
  `last_changed` -- the moment any device toggles, its own timestamp resets,
  so we persist `room_state.all_on_since` instead (see backend/state/store.py
  and backend/db/schema.sql) and maintain it here as rooms transition in and
  out of "fully on."

Both kinds share the same debounce mechanism (`room_state.last_hours_alert` /
`last_alert_at`) and both use the *virtual* clock throughout, so a demo run
(SIM_TIME_SCALE=60) compresses the 30-minute debounce and the 2-hour window
exactly as it compresses everything else.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta
from typing import Optional

from backend.config import OFFICE_HOURS_END, OFFICE_HOURS_START
from backend.sim.clock import SimClock
from backend.sim.clock import clock as default_clock
from backend.state import store
from backend.state.bus import Bus
from backend.state.bus import bus as default_bus
from backend.state.models import Alert, RoomSnapshot

DEBOUNCE = timedelta(minutes=30)
ALL_ON_THRESHOLD = timedelta(hours=2)


def _parse_hhmm(value: str) -> time:
    hour, minute = (int(part) for part in value.split(":"))
    return time(hour, minute)


OFFICE_START_TIME = _parse_hhmm(OFFICE_HOURS_START)
OFFICE_END_TIME = _parse_hhmm(OFFICE_HOURS_END)


def within_office_hours(now: datetime) -> bool:
    return OFFICE_START_TIME <= now.time() < OFFICE_END_TIME


def _debounce_elapsed(last_fired_iso: Optional[str], now: datetime) -> bool:
    if last_fired_iso is None:
        return True
    last_fired = datetime.fromisoformat(last_fired_iso)
    return (now - last_fired) >= DEBOUNCE


def _describe_on_devices(room_snapshot: RoomSnapshot) -> str:
    fans_on = sum(1 for d in room_snapshot.devices if d.kind == "fan" and d.state == "on")
    lights_on = sum(1 for d in room_snapshot.devices if d.kind == "light" and d.state == "on")
    parts = []
    if fans_on:
        parts.append(f"{fans_on} fan{'s' if fans_on != 1 else ''}")
    if lights_on:
        parts.append(f"{lights_on} light{'s' if lights_on != 1 else ''}")
    return " and ".join(parts) if parts else "no devices"


def _after_hours_message(room_snapshot: RoomSnapshot, now: datetime) -> str:
    devices_desc = _describe_on_devices(room_snapshot)
    return f"{room_snapshot.room.name} still has {devices_desc} ON and it's {now.strftime('%H:%M')}."


def _all_on_2h_message(room_snapshot: RoomSnapshot, all_on_since: datetime, approx: bool) -> str:
    since_str = all_on_since.strftime("%H:%M")
    suffix = " (approx. -- reconstructed at evaluator startup, exact transition unknown)" if approx else ""
    return f"{room_snapshot.room.name} has had all devices ON continuously since {since_str}{suffix}."


class AlertEvaluator:
    def __init__(
        self,
        *,
        interval_seconds: float = 30.0,
        clock: Optional[SimClock] = None,
        bus_: Optional[Bus] = None,
        db_path: Optional[str] = None,
    ) -> None:
        self.interval_seconds = interval_seconds
        self.db_path = db_path
        self._clock = clock or default_clock
        self._bus = bus_ or default_bus
        self._task: Optional[asyncio.Task] = None
        self._running = False
        # Rooms whose current all_on_since was reconstructed at startup rather
        # than observed live -- see _bootstrap_all_on_since below.
        self._approx_rooms: set[str] = set()

    def start(self) -> None:
        if self._task is not None:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_with_bootstrap())

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run_with_bootstrap(self) -> None:
        await self._bootstrap_all_on_since()
        await self._run_forever()

    async def _run_forever(self) -> None:
        while self._running:
            await self.tick()
            await asyncio.sleep(self.interval_seconds)

    async def _bootstrap_all_on_since(self) -> None:
        """Best-effort reconciliation run once, when the evaluator starts.

        A normal restart mid-window is already handled correctly with no
        special-casing: room_state.all_on_since is written through to SQLite
        the moment it's set, so it survives a process restart on its own. This
        method only covers the edge case PROJECT_PLAN.md §4.2 calls out
        explicitly: a room is *already* fully on the first time the evaluator
        ever looks at it, with no persisted all_on_since to explain when that
        started (e.g. right after seeding, or a room_state reset independent
        of device state). The best conservative estimate available is the
        latest `last_changed` among the room's devices -- that's the moment
        the last still-off device turned on, which is necessarily when the
        room became fully on. Rooms reconciled this way are flagged 'approx.'
        in their eventual all_on_2h message, per the plan.
        """
        snapshot = await store.get_snapshot(db_path=self.db_path)
        for room_snapshot in snapshot.rooms:
            devices = room_snapshot.devices
            all_on = bool(devices) and all(d.state == "on" for d in devices)
            if not all_on:
                continue

            room_state = await store.get_room_state(room_snapshot.room.id, db_path=self.db_path)
            if room_state and room_state.all_on_since is not None:
                continue  # already tracked correctly -- nothing to reconcile

            derived = max(datetime.fromisoformat(d.last_changed) for d in devices)
            self._approx_rooms.add(room_snapshot.room.id)
            await store.set_room_all_on_since(
                room_snapshot.room.id, derived.isoformat(), db_path=self.db_path
            )

    async def tick(self) -> list[dict]:
        """One evaluation pass across all rooms. Returns the alerts that fired
        this tick (mainly for tests/diagnostics -- production code doesn't
        need the return value, it subscribes to the bus like everything
        else)."""
        now = self._clock.now()
        fired: list[dict] = []
        snapshot = await store.get_snapshot(db_path=self.db_path)

        for room_snapshot in snapshot.rooms:
            room_id = room_snapshot.room.id
            devices = room_snapshot.devices
            all_on = bool(devices) and all(d.state == "on" for d in devices)
            any_on = any(d.state == "on" for d in devices)

            room_state = await store.get_room_state(room_id, db_path=self.db_path)
            all_on_since_iso = room_state.all_on_since if room_state else None
            last_hours_alert = room_state.last_hours_alert if room_state else None
            last_all_on_alert = room_state.last_alert_at if room_state else None

            # 1. Maintain the all_on_since transition.
            if all_on and all_on_since_iso is None:
                all_on_since_iso = now.isoformat()
                await store.set_room_all_on_since(room_id, all_on_since_iso, db_path=self.db_path)
            elif not all_on and all_on_since_iso is not None:
                all_on_since_iso = None
                self._approx_rooms.discard(room_id)
                await store.set_room_all_on_since(room_id, None, db_path=self.db_path)

            # 2. after_hours -- any device on outside office hours, debounced per room.
            if any_on and not within_office_hours(now):
                if _debounce_elapsed(last_hours_alert, now):
                    message = _after_hours_message(room_snapshot, now)
                    alert = await self._fire(room_id, "after_hours", message, now)
                    fired.append(alert)

            # 3. all_on_2h -- all devices on continuously for >= 2h, debounced per room.
            if all_on_since_iso is not None:
                all_on_since = datetime.fromisoformat(all_on_since_iso)
                if now - all_on_since >= ALL_ON_THRESHOLD and _debounce_elapsed(
                    last_all_on_alert, now
                ):
                    approx = room_id in self._approx_rooms
                    message = _all_on_2h_message(room_snapshot, all_on_since, approx)
                    alert = await self._fire(room_id, "all_on_2h", message, now)
                    fired.append(alert)

        return fired

    async def _fire(self, room_id: str, kind: str, message: str, now: datetime) -> dict:
        ts = now.isoformat()
        alert_id = await store.insert_alert(
            room_id=room_id,
            device_id=None,
            kind=kind,
            message=message,
            ts=ts,
            db_path=self.db_path,
        )
        await store.set_room_last_alert_at(room_id, kind, ts, db_path=self.db_path)

        alert = Alert(
            id=alert_id,
            room_id=room_id,
            device_id=None,
            kind=kind,
            message=message,
            created_at=ts,
            acked=False,
        )
        # Alert's field names already match docs/API_CONTRACT.md §4 exactly
        # (unlike Device, no alias/exclude massaging is needed here).
        self._bus.publish({"type": "alert_new", "payload": {"alert": alert.model_dump()}})
        return alert.model_dump()
