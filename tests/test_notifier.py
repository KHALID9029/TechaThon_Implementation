"""Tests for backend/bot/notifier.py -- the pro-active Discord notifier
(Phase A7).

post_alert() is tested against a fake bot/channel (no real discord.py client,
no network). Notifier's bus-subscription + debounce logic is tested both
directly (calling _handle_alert) and end-to-end through a real Bus, using the
same monkeypatched-time.monotonic() pattern as tests/test_alerts.py for
deterministic debounce-window assertions.
"""
from __future__ import annotations

import asyncio
import dataclasses
from datetime import datetime
from zoneinfo import ZoneInfo

from backend.bot import notifier as notifier_module
from backend.sim.clock import SimClock
from backend.state.bus import Bus

DHAKA = ZoneInfo("Asia/Dhaka")

ALERT = {
    "id": 1,
    "room_id": "work2",
    "device_id": None,
    "kind": "after_hours",
    "message": "Work Room 2 still has 2 fans ON and it's 21:14.",
    "created_at": "2026-07-03T21:14:00+06:00",
    "acked": False,
}


def _with_discord_configured(monkeypatch, **overrides):
    defaults = {"discord_token": "test-token", "alert_channel_id": "12345", "gemini_api_key": None}
    defaults.update(overrides)
    monkeypatch.setattr(
        notifier_module, "config", dataclasses.replace(notifier_module.config, **defaults)
    )


class _FakeChannel:
    def __init__(self, *, send_raises: bool = False):
        self.sent: list[str] = []
        self._send_raises = send_raises

    async def send(self, text: str) -> None:
        if self._send_raises:
            raise RuntimeError("discord API error")
        self.sent.append(text)


class _FakeBot:
    def __init__(self, channel=None, *, cache_hit: bool = True, fetch_raises: bool = False):
        self._channel = channel
        self._cache_hit = cache_hit
        self._fetch_raises = fetch_raises
        self.fetch_calls = 0

    def get_channel(self, channel_id):
        return self._channel if self._cache_hit else None

    async def fetch_channel(self, channel_id):
        self.fetch_calls += 1
        if self._fetch_raises:
            raise RuntimeError("channel not found")
        return self._channel


# --- post_alert --------------------------------------------------------


async def test_post_alert_sends_via_cached_channel(monkeypatch):
    _with_discord_configured(monkeypatch)
    channel = _FakeChannel()
    bot = _FakeBot(channel, cache_hit=True)

    sent = await notifier_module.post_alert(bot, 12345, ALERT)

    assert sent is True
    assert bot.fetch_calls == 0
    assert channel.sent == [ALERT["message"]]  # no gemini key -> humanize() passes text through


async def test_post_alert_falls_back_to_fetch_channel_on_cache_miss(monkeypatch):
    _with_discord_configured(monkeypatch)
    channel = _FakeChannel()
    bot = _FakeBot(channel, cache_hit=False)

    sent = await notifier_module.post_alert(bot, 12345, ALERT)

    assert sent is True
    assert bot.fetch_calls == 1
    assert channel.sent == [ALERT["message"]]


async def test_post_alert_returns_false_when_channel_cannot_be_found(monkeypatch):
    bot = _FakeBot(None, cache_hit=False, fetch_raises=True)

    sent = await notifier_module.post_alert(bot, 12345, ALERT)

    assert sent is False


async def test_post_alert_returns_false_when_send_fails(monkeypatch):
    channel = _FakeChannel(send_raises=True)
    bot = _FakeBot(channel, cache_hit=True)

    sent = await notifier_module.post_alert(bot, 12345, ALERT)

    assert sent is False


async def test_post_alert_humanizes_the_message(monkeypatch):
    channel = _FakeChannel()
    bot = _FakeBot(channel, cache_hit=True)

    async def fake_humanize(text: str) -> str:
        return f"[friendly] {text}"

    monkeypatch.setattr(notifier_module.llm, "humanize", fake_humanize)

    await notifier_module.post_alert(bot, 12345, ALERT)
    assert channel.sent == [f"[friendly] {ALERT['message']}"]


# --- Notifier.start() gating -------------------------------------------


async def test_notifier_start_noops_without_discord_token(monkeypatch):
    _with_discord_configured(monkeypatch, discord_token=None)
    bus = Bus()
    notifier = notifier_module.Notifier(bot=_FakeBot(_FakeChannel()), bus_=bus)

    notifier.start()

    assert bus.subscriber_count == 0
    await notifier.stop()  # must be a safe no-op


async def test_notifier_start_noops_without_channel_id(monkeypatch):
    _with_discord_configured(monkeypatch, alert_channel_id=None)
    bus = Bus()
    notifier = notifier_module.Notifier(bot=_FakeBot(_FakeChannel()), bus_=bus)

    notifier.start()

    assert bus.subscriber_count == 0


async def test_notifier_stop_is_safe_when_never_started():
    notifier = notifier_module.Notifier(bot=_FakeBot(_FakeChannel()), bus_=Bus())
    await notifier.stop()


# --- _handle_alert debounce ----------------------------------------------


def _make_notifier(bot, *, clock=None, bus_=None):
    return notifier_module.Notifier(
        bot=bot, channel_id="12345", clock=clock, bus_=bus_ or Bus()
    )


async def test_handle_alert_posts_and_returns_true(monkeypatch):
    _with_discord_configured(monkeypatch)
    channel = _FakeChannel()
    clock = SimClock(
        tz_name="Asia/Dhaka", start_time=datetime(2026, 7, 3, 21, 14, 0, tzinfo=DHAKA), scale=1.0
    )
    notifier = _make_notifier(_FakeBot(channel), clock=clock)

    sent = await notifier._handle_alert(ALERT)

    assert sent is True
    assert channel.sent == [ALERT["message"]]


async def test_handle_alert_debounces_same_room_and_kind(monkeypatch):
    _with_discord_configured(monkeypatch)
    channel = _FakeChannel()
    clock = SimClock(
        tz_name="Asia/Dhaka", start_time=datetime(2026, 7, 3, 21, 14, 0, tzinfo=DHAKA), scale=1.0
    )
    notifier = _make_notifier(_FakeBot(channel), clock=clock)

    first = await notifier._handle_alert(ALERT)
    second = await notifier._handle_alert(ALERT)

    assert first is True
    assert second is False
    assert channel.sent == [ALERT["message"]]  # only posted once


async def test_handle_alert_reposts_after_debounce_window_elapses(monkeypatch):
    _with_discord_configured(monkeypatch)
    anchor = [0.0]
    monkeypatch.setattr("backend.sim.clock.time.monotonic", lambda: anchor[0])
    channel = _FakeChannel()
    start = datetime(2026, 7, 3, 21, 14, 0, tzinfo=DHAKA)
    clock = SimClock(tz_name="Asia/Dhaka", start_time=start, scale=1.0)
    notifier = _make_notifier(_FakeBot(channel), clock=clock)

    first = await notifier._handle_alert(ALERT)
    anchor[0] = 31 * 60  # 31 real seconds later == 31 virtual minutes at scale=1.0
    second = await notifier._handle_alert(ALERT)

    assert first is True
    assert second is True
    assert channel.sent == [ALERT["message"], ALERT["message"]]


async def test_handle_alert_does_not_debounce_across_different_rooms_or_kinds(monkeypatch):
    _with_discord_configured(monkeypatch)
    channel = _FakeChannel()
    clock = SimClock(
        tz_name="Asia/Dhaka", start_time=datetime(2026, 7, 3, 21, 14, 0, tzinfo=DHAKA), scale=1.0
    )
    notifier = _make_notifier(_FakeBot(channel), clock=clock)

    alert_a = dict(ALERT, room_id="work2", kind="after_hours")
    alert_b = dict(ALERT, room_id="drawing", kind="after_hours")
    alert_c = dict(ALERT, room_id="work2", kind="all_on_2h")

    assert await notifier._handle_alert(alert_a) is True
    assert await notifier._handle_alert(alert_b) is True
    assert await notifier._handle_alert(alert_c) is True
    assert len(channel.sent) == 3


# --- end-to-end via the real Bus -----------------------------------------


async def test_notifier_consumes_alert_new_events_from_the_bus(monkeypatch):
    _with_discord_configured(monkeypatch)
    channel = _FakeChannel()
    bot = _FakeBot(channel)
    bus = Bus()
    clock = SimClock(
        tz_name="Asia/Dhaka", start_time=datetime(2026, 7, 3, 21, 14, 0, tzinfo=DHAKA), scale=1.0
    )
    notifier = notifier_module.Notifier(bot=bot, channel_id="12345", clock=clock, bus_=bus)

    notifier.start()
    try:
        assert bus.subscriber_count == 1
        bus.publish({"type": "alert_new", "payload": {"alert": ALERT}})

        for _ in range(20):
            await asyncio.sleep(0)
            if channel.sent:
                break

        assert channel.sent == [ALERT["message"]]
    finally:
        await notifier.stop()
        assert bus.subscriber_count == 0


async def test_notifier_ignores_non_alert_events(monkeypatch):
    _with_discord_configured(monkeypatch)
    channel = _FakeChannel()
    bot = _FakeBot(channel)
    bus = Bus()
    notifier = notifier_module.Notifier(bot=bot, channel_id="12345", bus_=bus)

    notifier.start()
    try:
        bus.publish({"type": "usage_tick", "payload": {"total_watts": 100}})
        for _ in range(5):
            await asyncio.sleep(0)
        assert channel.sent == []
    finally:
        await notifier.stop()
