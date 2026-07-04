# Office Electrical Monitor

**Lights, Fans, Discord: The Boss's Big Idea**

A single FastAPI backend simulates 15 electrical devices (2 fans + 3 lights ×
3 rooms) on a virtual office clock, and surfaces the live state through two
clients reading from one shared source of truth: a real-time web dashboard
(WebSocket) and a Discord bot (commands + pro-active alert posts, humanized
by Gemini).

![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-async-009688)
![SQLite](https://img.shields.io/badge/SQLite-aiosqlite-003B57)
![discord.py](https://img.shields.io/badge/discord.py-bot-5865F2)
![Gemini](https://img.shields.io/badge/Gemini-humanizer-8E75FF)
![License](https://img.shields.io/badge/license-MIT-green)

## Quickstart

```bash
git clone <repo-url>
cd office-electricity-monitor
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
```

Then, the 4 commands that get you running:

```bash
pip install -r requirements.txt
cp .env.example .env                # fill in DISCORD_TOKEN / GEMINI_API_KEY if you want the bot + humanizer live -- see below
python -m scripts.seed_db
uvicorn backend.main:app --reload --port 8000
```

Open **http://localhost:8000/** — the backend serves the dashboard, the
REST API, and the WebSocket all from one origin (no CORS, no `file://`
issues). Don't open `frontend/index.html` directly — it needs the same
origin as the API to work.

In Discord (once `DISCORD_TOKEN` is set): `!status`, `!room work1`,
`!usage`, `!help`.

The dashboard's "today's usage" doesn't start at exactly 0 kWh — a fresh
database is seeded with a small (2.5 kWh) placeholder baseline so the demo
doesn't look stuck at zero for the first few minutes. Real usage accumulates
on top of that from then on, and restarting the app never adds the baseline
again.

## Getting the optional credentials

The app runs fully without any of these — the dashboard works standalone
and the bot just logs a warning and stays off. Fill in whichever you want
live.

**`DISCORD_TOKEN`** (turns the bot on)
1. Go to the [Discord Developer Portal](https://discord.com/developers/applications) and click **New Application**.
2. Open the **Bot** tab → **Reset Token** → copy it into `.env`.
3. Still on the **Bot** tab, turn on **Message Content Intent** under Privileged Gateway Intents (the bot needs this to read `!` commands).
4. Go to **OAuth2 → URL Generator**, check the **bot** scope and permissions like *Send Messages* and *Read Message History*, then open the generated URL to invite the bot to your server.

**`ALERT_CHANNEL_ID`** (where alerts get posted)
1. In Discord, go to **User Settings → Advanced** and turn on **Developer Mode**.
2. Right-click the channel you want alerts in → **Copy Channel ID** → paste into `.env`.

**`GEMINI_API_KEY`** (humanizes bot messages)
1. Go to [Google AI Studio](https://aistudio.google.com/app/apikey) and sign in.
2. Click **Create API key** → copy it into `.env`.
3. Leave it blank to disable the humanizer entirely — no network call is made and messages are sent as plain templated text instead.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `DB_PATH` | `./office.db` | SQLite file (git-ignored, per-machine) |
| `OFFICE_TZ` | `Asia/Dhaka` | Timezone the virtual clock and office hours use |
| `SIM_TICK_MS` | `5000` (code default), shipped as `30000` in `.env.example` | How often, in real milliseconds, each device re-rolls its on/off state. Higher = calmer, easier to follow live (e.g. in `!status`) |
| `SIM_START_TIME` | *(empty)* | ISO8601; empty = virtual clock behaves like real wall-clock time |
| `SIM_TIME_SCALE` | `1` | `>1` speeds up the virtual clock for demos (see below) |
| `DISCORD_TOKEN` | *(empty)* | Bot token; bot stays off with a log warning if unset |
| `ALERT_CHANNEL_ID` | *(empty)* | Discord channel the pro-active notifier posts to |
| `GEMINI_API_KEY` | *(empty)* | Enables the humanizer; leave empty to disable (see above) |
| `GEMINI_MODEL` | `gemini-3.5-flash` | Model name, always read from env, never hard-coded |

`SIM_TICK_MS` and `SIM_TIME_SCALE` are independent knobs: the former controls
how often devices toggle (visual/Discord pacing), the latter controls how
fast virtual time — and therefore kWh accounting — moves. Tuning one doesn't
affect the other.

## How it works

15 simulated devices feed a `Simulator` task, which writes state to SQLite
and publishes events onto an in-process bus. Two independent consumers read
that bus: the FastAPI WebSocket route (pushing live updates to the web
dashboard) and the Discord bot's notifier (posting alerts to a channel,
humanized by Gemini). A separate `AlertEvaluator` task ticks on its own
timer, checking room state for two alert conditions:

- **`after_hours`** — any device in the room is `on` outside office hours
  (09:00–17:00 by default). Debounced 30 virtual minutes per room.
- **`all_on_2h`** — every device in a room has been continuously `on` for at
  least 2 virtual hours. Debounced the same way.

Both alert kinds are pushed live to the dashboard's alerts panel and posted
to Discord (humanized by Gemini) if `DISCORD_TOKEN` and `ALERT_CHANNEL_ID`
are set.

For a demo, set `SIM_START_TIME` to an evening timestamp and
`SIM_TIME_SCALE=60` (1 real second = 1 virtual minute) — you'll see an
`after_hours` alert almost immediately and an `all_on_2h` alert within about
2 real minutes, instead of waiting for real office hours to actually pass.

See [`docs/diagrams/system_architecture.png`](docs/diagrams/system_architecture.png)
and [`docs/diagrams/data_flow.png`](docs/diagrams/data_flow.png) for visual
walkthroughs, and [`docs/diagrams/FIGMA_LINK.md`](docs/diagrams/FIGMA_LINK.md)
for the live, editable Figma source.

### The Gemini humanizer

Discord bot replies and alert posts are built as plain templated text first,
then optionally passed through `backend/bot/llm.py`'s `humanize()`:

- If `GEMINI_API_KEY` is unset, `humanize()` returns the raw text
  immediately — **no network call is made at all.** This is the easiest way
  to disable it.
- If the key is set but the call fails for any reason (timeout, non-2xx,
  malformed response), it silently falls back to the raw text. The bot
  never hangs or crashes because of an LLM outage.
- The model name always comes from `GEMINI_MODEL` — never hard-coded, since
  Gemini model names get deprecated over time.

## Hardware

See [`hardware/tinkercad/README.md`](hardware/tinkercad/README.md) for the
Tinkercad circuit schematic, sketch, and screenshot.

## A note on the "15 devices" count

The brief's text says "6 devices per room / 18 total," but its own
floor-plan legend tallies 6 fans + 9 lights = **15**, and "2 fans + 3
lights per room" is stated three times unambiguously. We build **15**
devices (kept data-driven in `scripts/seed_db.py`, so the composition is a
one-line change if that interpretation is ever wrong) and render "2 fans, 3
lights" per room everywhere — in `!status`, `!room`, and the dashboard —
matching the brief's own command example exactly.

## Testing

```bash
pytest -q
```

Covers the state store, the virtual clock, the simulator, the alert
evaluator (pinned-clock, deterministic), the API/WebSocket layer, the
Discord command builders, the Gemini humanizer's fallback path, and the
pro-active notifier's debounce logic.

## License

MIT — see [`LICENSE`](LICENSE).
