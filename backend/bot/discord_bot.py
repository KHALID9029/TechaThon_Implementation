"""Discord bot client (PROJECT_PLAN.md §8, EXECUTION_PHASES.md Phases A5/A6).

Runs as an asyncio task inside backend/main.py's lifespan -- same process and
event loop as the simulator and alert evaluator, so there is exactly one
source of truth (PROJECT_PLAN.md §1: everything reads through backend/state/
store.py). Replies are built as raw templated text by backend/bot/commands.py,
then passed through backend/bot/llm.humanize() (Phase A6) for a friendlier
tone -- humanize() itself falls back to the raw text on any error or missing
API key, so this call never raises and never blocks the fallback path.
"""
from __future__ import annotations

import logging

import discord
from discord.ext import commands as discord_commands

from backend.bot import commands as bot_commands
from backend.bot import llm
from backend.config import config

logger = logging.getLogger(__name__)

INTENTS = discord.Intents.default()
INTENTS.message_content = True  # required to read command text (discord.py >= 2.0)


class OfficeBot(discord_commands.Bot):
    def __init__(self) -> None:
        super().__init__(command_prefix="!", intents=INTENTS, help_command=None)

    async def setup_hook(self) -> None:
        self.add_command(status_command)
        self.add_command(room_command)
        self.add_command(usage_command)
        self.add_command(help_command)

    async def on_ready(self) -> None:
        logger.info(
            "OfficeBot ready: logged in as %s, in %d guild(s).", self.user, len(self.guilds)
        )


@discord_commands.command(name="status")
async def status_command(ctx: discord_commands.Context) -> None:
    """!status -- PROJECT_PLAN.md §8: office-wide summary of every room,
    humanized by Gemini (falls back to raw text on any LLM error)."""
    async with ctx.typing():
        text = await bot_commands.fetch_status_text()
        reply = await llm.humanize(text)
    await ctx.send(reply)


@discord_commands.command(name="room")
async def room_command(ctx: discord_commands.Context, *, name: str = "") -> None:
    """!room <name> -- PROJECT_PLAN.md §8: single-room summary, lenient name
    matching, humanized by Gemini (falls back to raw text on any LLM error)."""
    if not name:
        await ctx.send("Usage: !room <name> (e.g. !room work1)")
        return
    async with ctx.typing():
        text = await bot_commands.fetch_room_text(name)
        reply = await llm.humanize(text)
    await ctx.send(reply)


@discord_commands.command(name="usage")
async def usage_command(ctx: discord_commands.Context) -> None:
    """!usage -- PROJECT_PLAN.md §8: current power draw + today's estimated
    kWh, humanized by Gemini (falls back to raw text on any LLM error)."""
    async with ctx.typing():
        text = await bot_commands.fetch_usage_text()
        reply = await llm.humanize(text)
    await ctx.send(reply)


@discord_commands.command(name="help")
async def help_command(ctx: discord_commands.Context) -> None:
    await ctx.send(bot_commands.build_help_text())


async def run_bot(bot: OfficeBot) -> None:
    """Entry point used by backend/main.py's lifespan, started as an asyncio
    task. If DISCORD_TOKEN isn't set, logs a warning and returns immediately
    instead of crashing the whole app -- lets the web dashboard + simulator
    run standalone during local dev without a bot token configured yet."""
    if not config.discord_token:
        logger.warning("DISCORD_TOKEN not set -- Discord bot will not start.")
        return
    async with bot:
        await bot.start(config.discord_token)
