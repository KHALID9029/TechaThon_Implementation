"""Deterministic scripted events layered on top of the probabilistic simulator
(PROJECT_PLAN.md §5, EXECUTION_PHASES.md Phase A2).

The tick-by-tick probability model makes a demo look "alive," but a judge
watching a 3-minute video needs a few guaranteed, memorable beats -- and one
guaranteed alert. These four virtual-time checkpoints provide that. Each fires
**at most once** (tracked by the `fired` flag below); after it fires, the
probabilistic simulator takes back over immediately -- these are one-shot
nudges, not permanent overrides.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from typing import Awaitable, Callable, Iterable

ApplyFn = Callable[[str, str], Awaitable[None]]  # (device_id, "on"|"off") -> None


@dataclass
class PreludeEvent:
    at: time
    description: str
    room_id: str
    kinds: tuple[str, ...]  # which device kinds in that room this event touches
    to_state: str
    fired: bool = False


def _default_events() -> list[PreludeEvent]:
    return [
        PreludeEvent(
            at=time(9, 0),
            description="Morning arrival: Work Room 1 turns all ON.",
            room_id="work1",
            kinds=("fan", "light"),
            to_state="on",
        ),
        PreludeEvent(
            at=time(12, 30),
            description=(
                "Lunch: Drawing Room lights go OFF (simplified from the brief's "
                "'blinks off for 30s' -- the probabilistic simulator naturally "
                "brings them back within its normal office-hours window, so no "
                "separate 'turn back on' event is needed)."
            ),
            room_id="drawing",
            kinds=("light",),
            to_state="off",
        ),
        PreludeEvent(
            at=time(18, 30),
            description="People leaving: Work Room 2 fans turn OFF.",
            room_id="work2",
            kinds=("fan",),
            to_state="off",
        ),
        PreludeEvent(
            at=time(20, 45),
            description=(
                "Intentional 'left on': Work Room 2 turns all ON, to trigger the "
                "after_hours alert live in the demo (and, if left running, the "
                "all_on_2h alert too, ~2 virtual hours later)."
            ),
            room_id="work2",
            kinds=("fan", "light"),
            to_state="on",
        ),
    ]


class Prelude:
    """Tracks which scripted events have fired and applies the ones that newly
    qualify each time `check()` is called."""

    def __init__(self, events: list[PreludeEvent] | None = None) -> None:
        self.events: list[PreludeEvent] = events if events is not None else _default_events()

    def reset(self) -> None:
        """Clear all fired flags -- mainly useful for tests that re-run a scenario."""
        for event in self.events:
            event.fired = False

    async def check(self, now: datetime, devices: Iterable, apply: ApplyFn) -> list[PreludeEvent]:
        """Fire every not-yet-fired event whose scripted time has passed.

        `devices` -- the current Device objects, used to find which device ids
        match an event's room_id + kinds.
        `apply(device_id, new_state)` -- awaited once per matching device.

        Returns the events that fired on this call (empty list most ticks).
        """
        fired_now: list[PreludeEvent] = []
        current_time = now.time()
        for event in self.events:
            if event.fired or current_time < event.at:
                continue
            for device in devices:
                if device.room_id == event.room_id and device.kind in event.kinds:
                    await apply(device.id, event.to_state)
            event.fired = True
            fired_now.append(event)
        return fired_now
