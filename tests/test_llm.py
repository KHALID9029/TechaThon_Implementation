"""Tests for backend/bot/llm.py -- the Gemini humanizer (Phase A6).

No real network calls are made: httpx.AsyncClient is replaced with a small
fake that records the request and returns a scripted response/exception. This
lets us verify the offline-fallback contract (PROJECT_PLAN.md §8/§18.1)
without depending on a live Gemini key or endpoint.
"""
from __future__ import annotations

from dataclasses import replace

import httpx

from backend import config as config_module
from backend.bot import llm as llm_module

RAW_TEXT = "Drawing Room: 1 fan ON, 2 lights ON."


def _set_config(monkeypatch, *, gemini_api_key, gemini_model="gemini-3.5-flash"):
    test_config = replace(
        config_module.config, gemini_api_key=gemini_api_key, gemini_model=gemini_model
    )
    monkeypatch.setattr(llm_module, "config", test_config)
    return test_config


class _FakeResponse:
    def __init__(self, *, json_body=None, status_error=False):
        self._json_body = json_body
        self._status_error = status_error

    def raise_for_status(self) -> None:
        if self._status_error:
            request = httpx.Request("POST", "https://example.invalid")
            response = httpx.Response(500, request=request)
            raise httpx.HTTPStatusError("boom", request=request, response=response)

    def json(self):
        return self._json_body


class _FakeAsyncClient:
    """Stands in for httpx.AsyncClient(...) used as `async with ... as client`."""

    calls: list[tuple[str, dict]] = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def post(self, url: str, json: dict):
        _FakeAsyncClient.calls.append((url, json))
        return _FakeAsyncClient.next_response()

    @staticmethod
    def next_response():  # overridden per-test via monkeypatch
        raise NotImplementedError


def _install_fake_client(monkeypatch, respond):
    """`respond` is a callable(url, json) -> _FakeResponse, or one that raises."""
    _FakeAsyncClient.calls = []

    class Client(_FakeAsyncClient):
        async def post(self, url: str, json: dict):
            _FakeAsyncClient.calls.append((url, json))
            return respond(url, json)

    monkeypatch.setattr(llm_module.httpx, "AsyncClient", Client)


# --- no API key: offline fallback, zero network calls -----------------------


async def test_humanize_returns_raw_text_and_makes_no_call_when_key_missing(monkeypatch):
    _set_config(monkeypatch, gemini_api_key=None)

    def _should_not_be_called(url, json):
        raise AssertionError("humanize() must not call the network without an API key")

    _install_fake_client(monkeypatch, _should_not_be_called)

    result = await llm_module.humanize(RAW_TEXT)
    assert result == RAW_TEXT


# --- success path -------------------------------------------------------


async def test_humanize_returns_gemini_text_on_success(monkeypatch):
    _set_config(monkeypatch, gemini_api_key="test-key")
    friendly = "The Drawing Room has a fan and two lights on right now!"

    def _respond(url, json):
        return _FakeResponse(
            json_body={"candidates": [{"content": {"parts": [{"text": friendly}]}}]}
        )

    _install_fake_client(monkeypatch, _respond)

    result = await llm_module.humanize(RAW_TEXT)
    assert result == friendly


async def test_humanize_uses_model_and_key_from_config_in_url(monkeypatch):
    _set_config(monkeypatch, gemini_api_key="my-secret-key", gemini_model="gemini-9.9-flash")

    captured = {}

    def _respond(url, json):
        captured["url"] = url
        captured["prompt"] = json["contents"][0]["parts"][0]["text"]
        return _FakeResponse(
            json_body={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}
        )

    _install_fake_client(monkeypatch, _respond)

    await llm_module.humanize(RAW_TEXT)
    assert "gemini-9.9-flash" in captured["url"]
    assert "my-secret-key" in captured["url"]
    assert RAW_TEXT in captured["prompt"]


# --- fallback paths -------------------------------------------------------


async def test_humanize_falls_back_on_http_error(monkeypatch):
    _set_config(monkeypatch, gemini_api_key="test-key")

    def _respond(url, json):
        return _FakeResponse(status_error=True)

    _install_fake_client(monkeypatch, _respond)

    result = await llm_module.humanize(RAW_TEXT)
    assert result == RAW_TEXT


async def test_humanize_falls_back_on_timeout(monkeypatch):
    _set_config(monkeypatch, gemini_api_key="test-key")

    def _respond(url, json):
        raise httpx.TimeoutException("timed out")

    _install_fake_client(monkeypatch, _respond)

    result = await llm_module.humanize(RAW_TEXT)
    assert result == RAW_TEXT


async def test_humanize_falls_back_on_malformed_response(monkeypatch):
    _set_config(monkeypatch, gemini_api_key="test-key")

    def _respond(url, json):
        return _FakeResponse(json_body={"unexpected": "shape"})

    _install_fake_client(monkeypatch, _respond)

    result = await llm_module.humanize(RAW_TEXT)
    assert result == RAW_TEXT


async def test_humanize_falls_back_when_gemini_returns_empty_text(monkeypatch):
    _set_config(monkeypatch, gemini_api_key="test-key")

    def _respond(url, json):
        return _FakeResponse(
            json_body={"candidates": [{"content": {"parts": [{"text": "   "}]}}]}
        )

    _install_fake_client(monkeypatch, _respond)

    result = await llm_module.humanize(RAW_TEXT)
    assert result == RAW_TEXT
