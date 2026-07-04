"""Tiny in-process pub/sub bus (PROJECT_PLAN.md §1 / §6).

Every subscriber -- the WebSocket route, the Discord bot's pro-active notifier,
tests -- gets its own `asyncio.Queue` and receives every event published, in
order. There is no persistence here; SQLite (backend/state/store.py) is the
durable source of truth. The bus only fans out "something just changed"
notifications to whoever happens to be listening right now.

Event shape (matches docs/API_CONTRACT.md §5 exactly, so the API layer built in
Phase A3 can forward these to a WebSocket with essentially no transformation):

    {"type": "device_change" | "usage_tick" | "alert_new", "payload": {...}}
"""
from __future__ import annotations

import asyncio
from typing import Any


class Bus:
    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue] = []

    def subscribe(self) -> asyncio.Queue:
        """Register a new subscriber. Returns a queue that receives every event
        published from this point on. Call unsubscribe(queue) when the
        subscriber goes away (e.g. a WebSocket disconnects) to avoid leaking
        queues for the lifetime of the process."""
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        if queue in self._subscribers:
            self._subscribers.remove(queue)

    def publish(self, event: dict[str, Any]) -> None:
        """Fan out `event` to every current subscriber. Non-blocking: queues
        are unbounded, so one slow subscriber can never stall the publisher
        (the simulator, the alert evaluator, etc.)."""
        for queue in self._subscribers:
            queue.put_nowait(event)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


# Module-level singleton shared by the whole running app -- the simulator and
# alert evaluator publish to this; the WS route and bot notifier subscribe to
# it. Tests should construct their own Bus() instead, for isolation.
bus = Bus()
