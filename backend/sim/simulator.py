"""Time-of-day-aware device simulator (PROJECT_PLAN.md §5, EXECUTION_PHASES.md
Phase A2).

Runs as a background asyncio task inside the FastAPI app's lifespan (wired up
in Phase A3). Each tick:

1. The deterministic prelude (backend/sim/prelude.py) fires any scripted
   checkpoint whose virtual time has just passed.
2. Every device *not* just touched by the prelude gets an independent,
   probability-weighted chance to toggle, keyed on the virtual clock's
   current hour -- office hours see far more activity than the middle of
   the night.
3. Every toggle writes through backend/state/store.py (the single source of
   truth) and publishes a `device_change` event on the bus.
4. Once per tick, power draw is integrated into today's daily_usage row and a
   `usage_tick` event is published, regardless of whether anything toggled.
"""
from __future__ import annotations

import asyncio
import random
from datetime import datetime
from typing import Optional

from backend.config import config
from backend.sim.clock import SimClock
from backend.sim.clock import clock as default_clock
from backend.sim.prelude import Prelude
from backend.state import store
from backend.state.bus import Bus
from backend.state.bus import bus as default_bus
from backend.state.models import Device

# Per-device-kind toggle probability by hour-of-day window (PROJECT_PLAN.md §5).
# (start_hour_inclusive, end_hour_exclusive, fan_probability, light_probability)
PROBABILITY_WINDOWS: list[tuple[int, int, float, float]] = [
    (0, 6, 0.02, 0.01),
    (6, 9, 0.10, 0.05),
    (9, 17, 0.30, 0.40),
    (17, 20, 0.18, 0.22),
    (20, 22, 0.10, 0.15),
    (22, 24, 0.05, 0.08),
]


def probability_for(kind: str, hour: int) -> float:
    """Look up the (fan|light) toggle probability for a given hour-of-day (0-23)."""
    for start, end, fan_p, light_p in PROBABILITY_WINDOWS:
        if start <= hour < end:
            return fan_p if kind == "fan" else light_p
    raise ValueError(f"hour {hour} not covered by any probability window")


class Simulator:
    """Owns the per-tick loop. `start()` spawns a background asyncio task;
    `stop()` cancels it cleanly. `tick()` can also be awaited directly (used by
    scripts/run_sim_only.py and the tests) without starting the background loop.
    """

    def __init__(
        self,
        *,
        tick_ms: Optional[int] = None,
        seed: Optional[int] = None,
        clock: Optional[SimClock] = None,
        bus_: Optional[Bus] = None,
        db_path: Optional[str] = None,
        prelude: Optional[Prelude] = None,
    ) -> None:
        self.tick_ms = tick_ms if tick_ms is not None else config.sim_tick_ms
        self.db_path = db_path
        self._clock = clock or default_clock
        self._bus = bus_ or default_bus
        self._rng = random.Random(seed)
        self._prelude = prelude if prelude is not None else Prelude()
        self._task: Optional[asyncio.Task] = None
        self._running = False

    def start(self) -> None:
        """Spawn the background tick loop. No-op if already started."""
        if self._task is not None:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_forever())

    async def stop(self) -> None:
        """Cancel the background tick loop and wait for it to finish."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run_forever(self) -> None:
        while self._running:
            await self.tick()
            await asyncio.sleep(self.tick_ms / 1000)

    async def tick(self) -> dict:
        """Run one simulation step. Returns a small summary dict -- mainly for
        scripts/run_sim_only.py to print and for tests to assert on; callers
        driving the real app don't need the return value, they just subscribe
        to the bus."""
        now = self._clock.now()
        snapshot = await store.get_snapshot(db_path=self.db_path)

        devices_by_id = {d.id: d for d in snapshot.devices}
        watts_by_room = {rs.room.id: rs.watts_now for rs in snapshot.rooms}
        total_watts = snapshot.watts_total
        forced_ids: set[str] = set()

        async def _force(device_id: str, new_state: str) -> None:
            nonlocal total_watts
            device = devices_by_id[device_id]
            total_watts = await self._apply_state_change(
                device, new_state, now, devices_by_id, watts_by_room, total_watts
            )
            forced_ids.add(device_id)

        # 1. Deterministic prelude checkpoints fire first and win the tick --
        #    forced devices are skipped in the probability roll below so a
        #    scripted beat can never be undone in the very same tick it fires.
        fired_events = await self._prelude.check(now, list(devices_by_id.values()), _force)

        # 2. Probabilistic per-device toggles.
        for device_id, device in list(devices_by_id.items()):
            if device_id in forced_ids:
                continue
            probability = probability_for(device.kind, now.hour)
            if self._rng.random() < probability:
                new_state = "off" if device.state == "on" else "on"
                total_watts = await self._apply_state_change(
                    device, new_state, now, devices_by_id, watts_by_room, total_watts
                )

        # 3. Integrate power draw and publish usage_tick every tick, whether or
        #    not anything toggled. Energy integration uses *virtual* elapsed
        #    seconds (tick_ms scaled by the clock's time-scale), so a demo's
        #    kWh figure reflects a full virtual day passing, not just the few
        #    real seconds of wall-clock time the tick actually took.
        scale = getattr(self._clock, "scale", 1.0)
        virtual_tick_seconds = (self.tick_ms / 1000) * scale
        watt_seconds = total_watts * virtual_tick_seconds
        today = now.date().isoformat()
        await store.add_daily_usage(watt_seconds, day=today, db_path=self.db_path)
        today_watt_seconds = await store.get_daily_usage(day=today, db_path=self.db_path)

        self._bus.publish(
            {
                "type": "usage_tick",
                "payload": {
                    "total_watts": total_watts,
                    "today_kwh": round(today_watt_seconds / 3_600_000, 3),
                    "server_time": now.isoformat(),
                },
            }
        )

        return {
            "server_time": now.isoformat(),
            "total_watts": total_watts,
            "fired_prelude_events": [e.description for e in fired_events],
        }

    async def _apply_state_change(
        self,
        device: Device,
        new_state: str,
        now: datetime,
        devices_by_id: dict[str, Device],
        watts_by_room: dict[str, int],
        total_watts: int,
    ) -> int:
        """Writes the new state through the store, updates the in-memory
        bookkeeping dicts in place, publishes a `device_change` event, and
        returns the new total_watts. No-ops (and returns total_watts unchanged)
        if the device is already in the requested state."""
        if device.state == new_state:
            return total_watts

        ts = now.isoformat()
        await store.update_device_state(device.id, new_state, ts=ts, db_path=self.db_path)

        delta = device.wattage if new_state == "on" else -device.wattage
        total_watts += delta
        watts_by_room[device.room_id] += delta

        updated = device.model_copy(update={"state": new_state, "last_changed": ts})
        devices_by_id[device.id] = updated

        # Contract's Device shape (docs/API_CONTRACT.md §4) has no room_id --
        # it's implied by nesting under a room. room_id is still present one
        # level up, in the device_change payload itself.
        device_payload = updated.model_dump(by_alias=True, exclude={"room_id"})

        self._bus.publish(
            {
                "type": "device_change",
                "payload": {
                    "device": device_payload,
                    "room_id": device.room_id,
                    "room_watts": watts_by_room[device.room_id],
                    "total_watts": total_watts,
                },
            }
        )
        return total_watts
