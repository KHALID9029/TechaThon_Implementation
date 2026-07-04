"""Throwaway mock backend for frontend dev (Phase B1). Serves the frozen contract
(../../api.md) with scripted fixture data so the dashboard can be built before the
real backend exists. Deleted/unused after Sync 1 -- never shipped.
"""
from __future__ import annotations

import asyncio
import copy
import json
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

FRONTEND_DIR = Path(__file__).resolve().parent.parent
FIXTURES_PATH = Path(__file__).resolve().parent / "fixtures.json"

# Matches the demo clock config in EXECUTION_PHASES.md §3: 1 real second = 1 virtual minute.
SIM_TIME_SCALE = 60

with FIXTURES_PATH.open() as f:
    FIXTURES = json.load(f)

state: dict = copy.deepcopy(FIXTURES["initial_snapshot"])
alerts: list[dict] = []
sockets: set[WebSocket] = set()

_sim_start = datetime.fromisoformat(state["server_time"])
_real_start = time.monotonic()


def virtual_now() -> datetime:
    elapsed = time.monotonic() - _real_start
    return _sim_start + timedelta(seconds=elapsed * SIM_TIME_SCALE)


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def recompute_totals() -> None:
    total = 0
    for room in state["rooms"]:
        room_watts = sum(d["wattage"] for d in room["devices"] if d["state"] == "on")
        room["watts"] = room_watts
        total += room_watts
    state["total_watts"] = total


def find_device(device_id: str) -> tuple[dict, dict]:
    for room in state["rooms"]:
        for device in room["devices"]:
            if device["id"] == device_id:
                return room, device
    raise KeyError(device_id)


async def broadcast(message: dict) -> None:
    dead = []
    for ws in sockets:
        try:
            await ws.send_json(message)
        except Exception:
            dead.append(ws)
    for ws in dead:
        sockets.discard(ws)


async def apply_event(event: dict) -> None:
    now = virtual_now()
    state["server_time"] = iso(now)

    if event["type"] == "device_change":
        room, device = find_device(event["device_id"])
        device["state"] = event["new_state"]
        device["last_changed"] = iso(now)
        recompute_totals()
        await broadcast({
            "type": "device_change",
            "payload": {
                "device": device,
                "room_id": room["id"],
                "room_watts": room["watts"],
                "total_watts": state["total_watts"],
            },
        })

    elif event["type"] == "usage_tick":
        state["today_kwh"] = round(state["today_kwh"] + 0.01, 2)
        await broadcast({
            "type": "usage_tick",
            "payload": {
                "total_watts": state["total_watts"],
                "today_kwh": state["today_kwh"],
                "server_time": state["server_time"],
            },
        })

    elif event["type"] == "alert_new":
        alert = {
            "id": len(alerts) + 1,
            "room_id": event.get("room_id"),
            "device_id": event.get("device_id"),
            "kind": event["kind"],
            "message": event["message"],
            "created_at": iso(now),
            "acked": False,
        }
        alerts.append(alert)
        await broadcast({"type": "alert_new", "payload": {"alert": alert}})


async def event_loop() -> None:
    events = FIXTURES["events"]
    loop_delay = FIXTURES.get("loop_delay_s", 5)
    while True:
        elapsed = 0.0
        for event in events:
            await asyncio.sleep(event["delay_s"] - elapsed)
            elapsed = event["delay_s"]
            await apply_event(event)
        if not FIXTURES.get("loop", True):
            return
        await asyncio.sleep(loop_delay)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(event_loop())
    yield
    task.cancel()


app = FastAPI(title="office-electricity-monitor mock backend", lifespan=lifespan)


@app.middleware("http")
async def no_cache(request, call_next):
    # Dev convenience: without this, browsers heuristically cache the static frontend
    # files (no Cache-Control from StaticFiles by default), so an edit + refresh during
    # development can silently keep serving the old JS/CSS.
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.get("/api/state")
async def get_state():
    return state


@app.get("/api/alerts")
async def get_alerts(limit: int = 50):
    return {"alerts": list(reversed(alerts))[:limit]}


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    sockets.add(websocket)
    await websocket.send_json({"type": "snapshot", "payload": state})
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        sockets.discard(websocket)


# Mounted last so it never shadows the /api and /ws routes above -- same rule as the
# real backend will follow per the contract.
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
