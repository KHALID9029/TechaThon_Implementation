"""Tests for backend/bot/commands.py -- the string builders behind !status,
!room, !usage, !help (Phase A5).

These test the pure text-building functions and the async fetch wrappers
against a seeded temp DB. They deliberately do NOT touch discord.py /
backend/bot/discord_bot.py: exercising a real Discord gateway connection needs
a live bot token and server, which isn't available in this sandbox -- that's
what Phase A5's "in a real Discord server" manual verification step is for.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from backend.bot.commands import (
    UNKNOWN_ROOM_TEXT,
    build_help_text,
    build_room_text,
    build_status_text,
    build_usage_text,
    fetch_room_text,
    fetch_status_text,
    fetch_usage_text,
    resolve_room_id,
)
from backend.sim.clock import SimClock
from backend.state import store

DHAKA = ZoneInfo("Asia/Dhaka")


@pytest.fixture
async def db_path(tmp_path: Path) -> str:
    path = str(tmp_path / "bot_test.db")
    await store.init_db(db_path=path)
    await store.seed_if_empty(db_path=path)
    return path


async def _turn_on(db_path: str, device_id: str) -> None:
    await store.update_device_state(device_id, "on", db_path=db_path)


# --- resolve_room_id ---------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("drawing", "drawing"),
        ("Drawing", "drawing"),
        ("Drawing Room", "drawing"),
        ("drawingroom", "drawing"),
        ("work1", "work1"),
        ("Work1", "work1"),
        ("work 1", "work1"),
        ("Work Room 1", "work1"),
        ("work2", "work2"),
        ("work 2", "work2"),
        ("Work Room 2", "work2"),
    ],
)
def test_resolve_room_id_lenient_matching(raw, expected):
    assert resolve_room_id(raw) == expected


@pytest.mark.parametrize("raw", ["work", "kitchen", "", "  ", "workroom3"])
def test_resolve_room_id_rejects_ambiguous_or_unknown(raw):
    assert resolve_room_id(raw) is None


# --- build_status_text --------------------------------------------------


async def test_status_text_all_off(db_path):
    snapshot = await store.get_snapshot(db_path=db_path)
    text = build_status_text(snapshot)
    assert text == "Drawing Room: all off. Work Room 1: all off. Work Room 2: all off."


async def test_status_text_matches_brief_example(db_path):
    # PROJECT_PLAN.md §8's canonical example:
    # "Drawing Room: 1 fan ON, 2 lights ON. Work Room 1: all off.
    #  Work Room 2: 2 fans ON, 3 lights ON."
    await _turn_on(db_path, "drawing.fan.1")
    await _turn_on(db_path, "drawing.light.1")
    await _turn_on(db_path, "drawing.light.2")
    await _turn_on(db_path, "work2.fan.1")
    await _turn_on(db_path, "work2.fan.2")
    await _turn_on(db_path, "work2.light.1")
    await _turn_on(db_path, "work2.light.2")
    await _turn_on(db_path, "work2.light.3")

    snapshot = await store.get_snapshot(db_path=db_path)
    text = build_status_text(snapshot)
    assert text == (
        "Drawing Room: 1 fan ON, 2 lights ON. "
        "Work Room 1: all off. "
        "Work Room 2: 2 fans ON, 3 lights ON."
    )


async def test_fetch_status_text_reflects_live_state(db_path):
    await _turn_on(db_path, "work1.light.1")
    text = await fetch_status_text(db_path=db_path)
    assert "Work Room 1: 1 light ON." in text


# --- build_room_text / fetch_room_text ----------------------------------


async def test_room_text_for_specific_room(db_path):
    await _turn_on(db_path, "work1.fan.1")
    snapshot = await store.get_snapshot(db_path=db_path)
    assert build_room_text(snapshot, "work1") == "Work Room 1: 1 fan ON."


async def test_room_text_unknown_room_id_returns_none(db_path):
    snapshot = await store.get_snapshot(db_path=db_path)
    assert build_room_text(snapshot, "kitchen") is None


async def test_fetch_room_text_resolves_lenient_names(db_path):
    await _turn_on(db_path, "work1.fan.1")
    text = await fetch_room_text("Work Room 1", db_path=db_path)
    assert text == "Work Room 1: 1 fan ON."


async def test_fetch_room_text_unknown_or_ambiguous_returns_helpful_message(db_path):
    assert await fetch_room_text("kitchen", db_path=db_path) == UNKNOWN_ROOM_TEXT
    assert await fetch_room_text("work", db_path=db_path) == UNKNOWN_ROOM_TEXT


# --- build_usage_text / fetch_usage_text --------------------------------


def test_build_usage_text_format():
    assert build_usage_text(740, 4.2) == (
        "Total power right now: 740W. Today's estimated usage: 4.2 kWh."
    )
    assert build_usage_text(0, 0.0) == (
        "Total power right now: 0W. Today's estimated usage: 0.0 kWh."
    )


async def test_fetch_usage_text_reflects_live_state(db_path):
    start = datetime(2026, 7, 3, 9, 0, 0, tzinfo=DHAKA)
    clock = SimClock(tz_name="Asia/Dhaka", start_time=start, scale=1.0)

    await _turn_on(db_path, "drawing.fan.1")  # 60W
    # 4.2 kWh == 4.2 * 3_600_000 watt-seconds
    await store.add_daily_usage(
        4.2 * 3_600_000, day=start.date().isoformat(), db_path=db_path
    )

    text = await fetch_usage_text(clock=clock, db_path=db_path)
    assert text == "Total power right now: 60W. Today's estimated usage: 4.2 kWh."


# --- build_help_text -----------------------------------------------------


def test_build_help_text_mentions_all_commands():
    text = build_help_text()
    for cmd in ["!status", "!room", "!usage", "!help"]:
        assert cmd in text
