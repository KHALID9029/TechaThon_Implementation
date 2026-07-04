"""WS /ws -- on connect sends a `snapshot` frame, then forwards every bus event
(`device_change`, `usage_tick`, `alert_new`) verbatim, per docs/API_CONTRACT.md §5.

The bus events published by backend/sim/simulator.py (and, from Phase A4,
backend/alerts/evaluator.py) are already shaped exactly like the contract's WS
frames, so this route does no reshaping of its own for those -- it just
forwards whatever the bus hands it.
"""
from __future__ import annotations

import asyncio
import contextlib

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.api.schemas import snapshot_to_api
from backend.sim.clock import clock
from backend.state import store
from backend.state.bus import bus

router = APIRouter()


@router.websocket("/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    queue = bus.subscribe()

    try:
        snapshot = await store.get_snapshot()
        today = clock.today()
        watt_seconds = await store.get_daily_usage(day=today)
        today_kwh = round(watt_seconds / 3_600_000, 3)
        await websocket.send_json(
            {
                "type": "snapshot",
                "payload": snapshot_to_api(
                    snapshot, server_time=clock.now_iso(), today_kwh=today_kwh
                ),
            }
        )

        async def sender() -> None:
            while True:
                event = await queue.get()
                await websocket.send_json(event)

        async def receiver() -> None:
            # We don't expect the frontend to send anything over this socket --
            # awaiting receive() is simply how Starlette surfaces a client
            # disconnect promptly, instead of the sender blocking forever on
            # queue.get() after the client is long gone.
            while True:
                await websocket.receive_text()

        sender_task = asyncio.create_task(sender())
        receiver_task = asyncio.create_task(receiver())
        done, pending = await asyncio.wait(
            {sender_task, receiver_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        for task in done:
            exc = task.exception()
            if exc is not None and not isinstance(exc, WebSocketDisconnect):
                raise exc
    except WebSocketDisconnect:
        pass
    finally:
        bus.unsubscribe(queue)
