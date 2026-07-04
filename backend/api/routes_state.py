"""GET /api/state -- full snapshot, shaped exactly per docs/API_CONTRACT.md §4."""
from __future__ import annotations

from fastapi import APIRouter

from backend.api.schemas import snapshot_to_api
from backend.sim.clock import clock
from backend.state import store

router = APIRouter()


@router.get("/api/state")
async def get_state() -> dict:
    snapshot = await store.get_snapshot()
    today = clock.today()
    watt_seconds = await store.get_daily_usage(day=today)
    today_kwh = round(watt_seconds / 3_600_000, 3)
    return snapshot_to_api(snapshot, server_time=clock.now_iso(), today_kwh=today_kwh)
