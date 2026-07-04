"""The virtual clock (docs/API_CONTRACT.md §2, PROJECT_PLAN.md §5).

Every timestamp anywhere in the backend -- device.last_changed, alert.created_at,
the daily_usage day boundary, the WS `server_time` field -- must come from this
module, never from `datetime.now()` directly. That single rule is what lets a
demo compress a whole office day (crossing "after hours", accumulating a 2h
continuous-on window) into a couple of real minutes by setting SIM_TIME_SCALE.

Two modes, selected by whether SIM_START_TIME is set:

- **Real mode** (SIM_START_TIME empty, the default): now() always returns the
  actual current time in OFFICE_TZ. SIM_TIME_SCALE is ignored in this mode.
- **Virtual mode** (SIM_START_TIME set): now() starts at SIM_START_TIME and
  advances at SIM_TIME_SCALE x real speed, e.g. SIM_TIME_SCALE=60 means 1 real
  second = 1 virtual minute.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from backend.config import config


@dataclass
class SimClock:
    """A clock that either mirrors real time or runs a scaled virtual timeline.

    Uses `time.monotonic()` (not wall-clock time) as the elapsed-time reference,
    so it isn't affected by NTP adjustments or the system clock changing under it.
    """

    tz_name: str = "Asia/Dhaka"
    start_time: datetime | None = None
    scale: float = 1.0

    def __post_init__(self) -> None:
        self._tz = ZoneInfo(self.tz_name)
        if self.start_time is not None and self.start_time.tzinfo is None:
            self.start_time = self.start_time.replace(tzinfo=self._tz)
        self._wall_anchor = time.monotonic()

    def now(self) -> datetime:
        """Current time -- real or virtual depending on how this clock was built."""
        if self.start_time is None:
            return datetime.now(self._tz)
        elapsed_real_seconds = time.monotonic() - self._wall_anchor
        virtual_elapsed = timedelta(seconds=elapsed_real_seconds * self.scale)
        return self.start_time + virtual_elapsed

    def now_iso(self) -> str:
        """`now()` formatted as ISO8601 with a timezone offset, e.g. the contract's
        `"2026-07-03T21:30:05+06:00"`."""
        return self.now().isoformat()

    def today(self) -> str:
        """Current virtual date as `YYYY-MM-DD`, for daily_usage's day key."""
        return self.now().date().isoformat()


def _parse_start_time(raw: str) -> datetime | None:
    if not raw:
        return None
    return datetime.fromisoformat(raw)


def _load_default_clock() -> SimClock:
    return SimClock(
        tz_name=config.office_tz,
        start_time=_parse_start_time(config.sim_start_time),
        scale=config.sim_time_scale,
    )


# Module-level singleton used by the rest of the app (simulator, alert evaluator,
# API routes, bot). Built once at import time from env/config. Tests should
# construct their own SimClock(...) instances instead of relying on this one, so
# they get deterministic, monkeypatch-friendly behavior independent of the
# environment the test suite happens to run in.
clock = _load_default_clock()


def now() -> datetime:
    """`from backend.sim.clock import now` -- convenience wrapper around the singleton."""
    return clock.now()


def now_iso() -> str:
    return clock.now_iso()


def today() -> str:
    return clock.today()
