# Office Electrical Monitor

Lights, Fans, Discord: The Boss's Big Idea — a single FastAPI backend simulates
15 electrical devices (2 fans + 3 lights × 3 rooms) across the office and
surfaces live state through two clients: a real-time web dashboard (WebSocket)
and a Discord bot, both reading from one shared source of truth.

Full design rationale lives in the team's local `PROJECT_PLAN.md` (not in this
repo). This README is the entry point for anyone cloning the repo cold.

## Quickstart

```bash
git clone <repo-url>
cd office-electricity-monitor
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # fill in DISCORD_TOKEN, GEMINI_API_KEY, ALERT_CHANNEL_ID
python -m scripts.seed_db
uvicorn backend.main:app --reload --port 8000
```

Open `frontend/index.html` (or `http://localhost:8000/`) for the dashboard.
In Discord: `!status`, `!room work1`, `!usage`, `!help`.

## Status

Repo bootstrapped — implementation follows the team's phased build plan.
See `docs/architecture_decision_record.md` for the architecture summary once added.

## License

MIT — see `LICENSE`.
