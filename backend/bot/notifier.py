"""Pro-active Discord notifier (PROJECT_PLAN.md §8, EXECUTION_PHASES.md Phase A7).

Subscribes to the bus (backend/state/bus.py) and, whenever an `alert_new`
event fires, posts a humanized version of the same alert to the configured
`ALERT_CHANNEL_ID` -- the alert that already reached the dashboard's alerts
panel over the WS `alert_new` frame (backend/api/routes_ws.py) also reaches
Discord, without either side knowing about the other.

Runs as its own asyncio task inside backend/main.py's lifespan, alongside the
Simulator and AlertEvaluator.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Optional

from backend.bot import llm
from backend.config import config
from backend.sim.clock import SimClock
from backend.sim.clock import clock as default_clock
from backend.state.bus import Bus
from backend.state.bus import bus as default_bus

logger = logging.getLogger(__name__)

# The alert evaluator (backend/alerts/evaluator.py) already debounces firing
# 30 (virtual) minutes per room+kind -- alert_new can't be published more
# often than that for the same room+kind. This is a second, independent
# debounce on the Discord-posting path specifically: defense-in-depth, so the
# notifier's own "no re-post within 30 min" guarantee doesn't silently depend
# on trusting every future alert_new publisher to have gotten its own
# debounce right. Kept in memory only (not persisted) -- a process restart
# re-posting one alert is an acceptable cost for not needing a new DB column
# just for this.
NOTIFIER_DEBOUNCE = timedelta(minutes=30)


async def post_alert(bot: Any, channel_id: int, alert: dict) -> bool:
    """Send a humanized message for `alert` (the dict shape from an alert_new
    bus event's payload["alert"], matching docs/API_CONTRACT.md §4) to
    `channel_id`. Returns True if the message was actually sent, False on any
    failure (channel not found/fetchable, send failed) -- it never raises, so
    a Discord hiccup can never take down the simulator/evaluator/dashboard.
    """
    try:
        channel = bot.get_channel(channel_id)
        if channel is None:
            channel = await bot.fetch_channel(channel_id)
    except Exception:
        logger.warning(
            "Could not fetch Discord channel %s for alert post.", channel_id, exc_info=True
        )
        return False

    text = await llm.humanize(alert["message"])

    try:
        await channel.send(text)
    except Exception:
        logger.warning("Failed to post alert to Discord channel %s.", channel_id, exc_info=True)
        return False
    return True


class Notifier:
    """Owns the bus subscription and the per-(room, kind) debounce described
    above. `start()` spawns a background asyncio task; `stop()` cancels it and
    unsubscribes from the bus."""

    def __init__(
        self,
        bot: Any,
        *,
        channel_id: Optional[str] = None,
        clock: Optional[SimClock] = None,
        bus_: Optional[Bus] = None,
        debounce: timedelta = NOTIFIER_DEBOUNCE,
    ) -> None:
        self.bot = bot
        self.channel_id = channel_id if channel_id is not None else config.alert_channel_id
        self._clock = clock or default_clock
        self._bus = bus_ or default_bus
        self._debounce = debounce
        self._queue: Optional[asyncio.Queue] = None
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._last_posted: dict[tuple[Optional[str], Optional[str]], datetime] = {}

    def start(self) -> None:
        if self._task is not None:
            return
        if not config.discord_token or not self.channel_id:
            logger.warning(
                "DISCORD_TOKEN or ALERT_CHANNEL_ID not set -- "
                "Discord alert notifier will not start."
            )
            return
        self._queue = self._bus.subscribe()
        self._running = True
        self._task = asyncio.create_task(self._run_forever())

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._queue is not None:
            self._bus.unsubscribe(self._queue)
            self._queue = None

    async def _run_forever(self) -> None:
        assert self._queue is not None
        while self._running:
            event = await self._queue.get()
            if event.get("type") != "alert_new":
                continue
            await self._handle_alert(event["payload"]["alert"])

    async def _handle_alert(self, alert: dict) -> bool:
        """Returns True if the alert was posted, False if it was debounced or
        the post failed -- mainly for tests; production code doesn't need the
        return value."""
        key = (alert.get("room_id"), alert.get("kind"))
        now = self._clock.now()
        last = self._last_posted.get(key)
        if last is not None and (now - last) < self._debounce:
            return False

        assert self.channel_id is not None  # guaranteed by start()'s guard
        sent = await post_alert(self.bot, int(self.channel_id), alert)
        if sent:
            self._last_posted[key] = now
        return sent
