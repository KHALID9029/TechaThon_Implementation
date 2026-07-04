"""Alert evaluator -- Phase A3 stub.

Runs on its own timer inside the FastAPI lifespan, same shape as the
Simulator, so Phase A4 can drop the real after_hours / all_on_2h logic into
`tick()` without touching main.py's wiring at all. For now `tick()` does
nothing -- no alerts fire yet.
"""
from __future__ import annotations

import asyncio
from typing import Optional


class AlertEvaluator:
    def __init__(self, *, interval_seconds: float = 30.0) -> None:
        self.interval_seconds = interval_seconds
        self._task: Optional[asyncio.Task] = None
        self._running = False

    def start(self) -> None:
        if self._task is not None:
            return
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

    async def _run_forever(self) -> None:
        while self._running:
            await self.tick()
            await asyncio.sleep(self.interval_seconds)

    async def tick(self) -> None:
        """Stub for Phase A3 -- full after_hours / all_on_2h logic (see
        PROJECT_PLAN.md §4.2) lands in Phase A4."""
        return None
