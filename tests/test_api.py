"""Tests for the FastAPI app (Phase A3): /healthz, /api/state, /api/alerts, and
the /ws snapshot + bus-forwarding flow -- all checked against docs/API_CONTRACT.md.

REST endpoints are tested with httpx.AsyncClient + ASGITransport (no lifespan
triggered, so no live Simulator/AlertEvaluator running in the background --
the DB is seeded once, manually, per test, giving a static and predictable
starting state). The WebSocket route is tested with FastAPI's TestClient
(httpx has no WebSocket support), also without triggering lifespan.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from backend import config as config_module
from backend.state import store as store_module
from backend.state.bus import bus as global_bus


@pytest.fixture
async def api_app(tmp_path: Path, monkeypatch):
    """Wires the real FastAPI app to an isolated temp DB.

    backend.state.store reads `config.db_path` as its default for every
    function call, so patching the `config` name inside that module's
    namespace is what actually redirects the app's routes to the temp DB.
    """
    db_path = str(tmp_path / "api_test.db")
    test_config = replace(config_module.config, db_path=db_path)
    monkeypatch.setattr(config_module, "config", test_config)
    monkeypatch.setattr(store_module, "config", test_config)

    await store_module.init_db(db_path=db_path)
    await store_module.seed_if_empty(db_path=db_path)

    from backend.main import app as fastapi_app

    yield fastapi_app, db_path


@pytest.fixture
async def client(api_app):
    fastapi_app, _ = api_app
    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# --- REST -------------------------------------------------------------------


async def test_healthz(client):
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


async def test_api_state_matches_contract_shape(client):
    response = await client.get("/api/state")
    assert response.status_code == 200
    data = response.json()

    assert set(data.keys()) == {"server_time", "office_hours", "total_watts", "today_kwh", "rooms"}
    assert data["office_hours"] == {"start": "09:00", "end": "17:00"}
    assert data["total_watts"] == 0  # freshly seeded -- everything off
    assert data["today_kwh"] == store_module.DEMO_BASELINE_KWH  # cosmetic seed baseline, not 0
    assert len(data["rooms"]) == 3

    room = data["rooms"][0]
    assert set(room.keys()) == {"id", "name", "watts", "devices"}
    assert len(room["devices"]) == 5

    device = room["devices"][0]
    assert set(device.keys()) == {
        "id", "kind", "index", "name", "state", "wattage", "last_changed",
    }
    assert "room_id" not in device


async def test_api_state_total_devices_is_15(client):
    response = await client.get("/api/state")
    data = response.json()
    total_devices = sum(len(room["devices"]) for room in data["rooms"])
    assert total_devices == 15
    fans = sum(1 for room in data["rooms"] for d in room["devices"] if d["kind"] == "fan")
    lights = sum(1 for room in data["rooms"] for d in room["devices"] if d["kind"] == "light")
    assert fans == 6
    assert lights == 9


async def test_api_alerts_empty_by_default(client):
    response = await client.get("/api/alerts")
    assert response.status_code == 200
    assert response.json() == {"alerts": []}


async def test_api_alerts_respects_limit_and_shape(client, api_app):
    _, db_path = api_app
    for i in range(3):
        await store_module.insert_alert(
            room_id="work1",
            device_id=None,
            kind="after_hours",
            message=f"alert {i}",
            db_path=db_path,
        )

    response = await client.get("/api/alerts?limit=2")
    data = response.json()
    assert len(data["alerts"]) == 2
    assert set(data["alerts"][0].keys()) == {
        "id", "room_id", "device_id", "kind", "message", "created_at", "acked",
    }
    # newest first
    assert data["alerts"][0]["message"] == "alert 2"


# --- WebSocket ----------------------------------------------------------------


async def test_ws_sends_snapshot_first_then_forwards_bus_events(api_app):
    fastapi_app, _ = api_app
    # No `with TestClient(app) as client:` -- that would trigger the real
    # lifespan (live Simulator + AlertEvaluator). Calling it bare skips
    # lifespan entirely, same as ASGITransport above, keeping this test
    # deterministic.
    test_client = TestClient(fastapi_app)

    with test_client.websocket_connect("/ws") as websocket:
        first = websocket.receive_json()
        assert first["type"] == "snapshot"
        assert set(first["payload"].keys()) == {
            "server_time", "office_hours", "total_watts", "today_kwh", "rooms",
        }

        synthetic_event = {
            "type": "device_change",
            "payload": {
                "device": {
                    "id": "drawing.fan.1",
                    "kind": "fan",
                    "index": 1,
                    "name": "Drawing Room Fan 1",
                    "state": "on",
                    "wattage": 60,
                    "last_changed": "2026-07-03T21:10:00+06:00",
                },
                "room_id": "drawing",
                "room_watts": 60,
                "total_watts": 60,
            },
        }
        global_bus.publish(synthetic_event)

        second = websocket.receive_json()
        assert second == synthetic_event


async def test_ws_forwards_usage_tick_and_alert_new_verbatim(api_app):
    fastapi_app, _ = api_app
    test_client = TestClient(fastapi_app)

    with test_client.websocket_connect("/ws") as websocket:
        websocket.receive_json()  # snapshot, ignored here

        usage_event = {
            "type": "usage_tick",
            "payload": {"total_watts": 300, "today_kwh": 1.234, "server_time": "2026-07-03T21:30:10+06:00"},
        }
        global_bus.publish(usage_event)
        assert websocket.receive_json() == usage_event

        alert_event = {
            "type": "alert_new",
            "payload": {
                "alert": {
                    "id": 1,
                    "room_id": "work2",
                    "device_id": None,
                    "kind": "after_hours",
                    "message": "Work Room 2 still fully on at 21:14.",
                    "created_at": "2026-07-03T21:14:00+06:00",
                    "acked": False,
                }
            },
        }
        global_bus.publish(alert_event)
        assert websocket.receive_json() == alert_event
