"""GET /api/alerts?limit=50 -- most recent alerts, newest first (docs/API_CONTRACT.md §3)."""
from __future__ import annotations

from fastapi import APIRouter, Query

from backend.api.schemas import alert_to_api
from backend.state import store

router = APIRouter()


@router.get("/api/alerts")
async def get_alerts(limit: int = Query(50, ge=1, le=500)) -> dict:
    alerts = await store.get_alerts(limit=limit)
    return {"alerts": [alert_to_api(a) for a in alerts]}
