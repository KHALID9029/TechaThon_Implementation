"""Gemini humanizer for Discord bot replies (PROJECT_PLAN.md §8,
EXECUTION_PHASES.md Phase A6).

Wraps the raw templated strings from backend/bot/commands.py in a friendlier
tone via Gemini. The model name always comes from config.gemini_model (env
GEMINI_MODEL) -- never hard-coded, since three different Gemini model names
were retired/deprecated during this project's own planning phase alone (see
PROJECT_PLAN.md §18.1). Any failure -- no API key, timeout, HTTP error,
malformed response -- falls back to the raw text so the bot never appears
frozen or crashes because of an LLM outage.
"""
from __future__ import annotations

import logging

import httpx

from backend.config import config

logger = logging.getLogger(__name__)

GEMINI_TIMEOUT_SECONDS = 6.0
MAX_REPLY_CHARS = 280

PROMPT_PREFIX = (
    "You are a friendly office-monitoring assistant. Rephrase the following "
    "status report in a warm, conversational tone. Keep your reply under "
    f"{MAX_REPLY_CHARS} characters. Do not invent any numbers, devices, or "
    "facts that are not already present in the text below. Reply with only "
    "the rephrased text, no preamble or quotes.\n\n"
)


def _endpoint_url(model: str, api_key: str) -> str:
    return (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={api_key}"
    )


async def humanize(text: str, *, timeout: float = GEMINI_TIMEOUT_SECONDS) -> str:
    """Rephrase `text` via Gemini. Returns `text` unchanged -- with no network
    call at all -- if GEMINI_API_KEY isn't set. Returns `text` unchanged on
    any error (timeout, non-2xx, malformed response) so callers never have to
    handle a failure case themselves."""
    if not config.gemini_api_key:
        return text

    url = _endpoint_url(config.gemini_model, config.gemini_api_key)
    body = {"contents": [{"parts": [{"text": PROMPT_PREFIX + text}]}]}

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, json=body)
            response.raise_for_status()
            data = response.json()
            humanized = data["candidates"][0]["content"]["parts"][0]["text"].strip()
            return humanized or text
    except Exception:
        logger.warning("Gemini humanize() failed; falling back to raw text.", exc_info=True)
        return text
