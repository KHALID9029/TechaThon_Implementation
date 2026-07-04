"""FastAPI application entry point: lifespan wiring, route registration, and
static frontend serving (docs/API_CONTRACT.md §1).

Run with:  uvicorn backend.main:app --reload --port 8000
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from backend.alerts.evaluator import AlertEvaluator
from backend.api import routes_alerts, routes_state, routes_ws
from backend.sim.simulator import Simulator
from backend.state import store


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await store.init_db()
    await store.seed_if_empty()

    simulator = Simulator()
    simulator.start()

    alert_evaluator = AlertEvaluator()
    alert_evaluator.start()

    app.state.simulator = simulator
    app.state.alert_evaluator = alert_evaluator

    try:
        yield
    finally:
        await simulator.stop()
        await alert_evaluator.stop()


app = FastAPI(title="Office Electrical Monitor", lifespan=lifespan)


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}


app.include_router(routes_state.router)
app.include_router(routes_alerts.router)
app.include_router(routes_ws.router)

# Mounted LAST: StaticFiles(html=True) at "/" would otherwise shadow the /api
# and /ws routes above it (docs/API_CONTRACT.md §1). This serves the frontend
# from the same origin as the API -- no CORS, no file:// problems, one process
# for the whole demo.
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
