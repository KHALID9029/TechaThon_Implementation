"""Environment-driven configuration for the whole backend.

Every other module (state store, simulator, API, bot) reads settings from the
single `config` object here instead of calling os.getenv directly, so there is
one place that knows about env var names and defaults.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

# Loads variables from a .env file in the current working directory (repo root)
# into os.environ. No-op if the file doesn't exist or a var is already set --
# real environment variables always win.
load_dotenv()


@dataclass(frozen=True)
class Config:
    db_path: str
    office_tz: str
    sim_tick_ms: int
    sim_start_time: str  # ISO8601, "" = virtual clock behaves like real wall-clock time
    sim_time_scale: float  # 1.0 = real time; >1 = demo speed-up (see docs/API_CONTRACT.md §2)
    discord_token: str | None
    alert_channel_id: str | None
    gemini_api_key: str | None
    gemini_model: str


def _load() -> Config:
    return Config(
        db_path=os.getenv("DB_PATH", "./office.db"),
        office_tz=os.getenv("OFFICE_TZ", "Asia/Dhaka"),
        sim_tick_ms=int(os.getenv("SIM_TICK_MS", "5000")),
        sim_start_time=os.getenv("SIM_START_TIME", ""),
        sim_time_scale=float(os.getenv("SIM_TIME_SCALE", "1")),
        discord_token=os.getenv("DISCORD_TOKEN") or None,
        alert_channel_id=os.getenv("ALERT_CHANNEL_ID") or None,
        gemini_api_key=os.getenv("GEMINI_API_KEY") or None,
        # gemini-2.5-flash is deprecated (shutdown 2026-10-16) -- see PROJECT_PLAN.md §18.1.
        # Do not change this default back to gemini-2.5-flash or gemini-1.5-flash.
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-3.5-flash"),
    )


config = _load()
