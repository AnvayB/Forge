# Fitness Accountability Coach

A Discord-first AI fitness accountability coach built around structured memory, event-driven
SQLite persistence, deterministic analytics, and a thin Discord adapter.

This is not a replacement for MyFitnessPal, Arrow, Apple Health, Garmin, or ChatGPT. It is a
long-term accountability partner that reduces decision fatigue, tracks only structured summaries,
and provides periodic reviews on the configured cadence.

## Architecture

- `src/fitness_coach/bot`: thin `discord.py` adapter.
- `src/fitness_coach/api`: FastAPI health and diagnostics surface.
- `src/fitness_coach/coach`: orchestration, OpenAI calls, and accountability flows.
- `src/fitness_coach/database`: SQLAlchemy models, session setup, and repositories.
- `src/fitness_coach/memory`: summarized durable memory.
- `src/fitness_coach/planner`: deterministic workout planning.
- `src/fitness_coach/analytics`: deterministic analytics from stored events.
- `src/fitness_coach/vision`: temporary image proof processing and retention policy.
- `src/fitness_coach/scheduler`: APScheduler jobs for check-ins and reviews.

## Setup

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

Fill in `.env` with your Discord bot token, OpenAI key, and database URL.

## Run

```bash
fitness-coach-api
fitness-coach-bot
```

The first startup initializes the SQLite schema. Runtime coaching behavior is loaded from
`config/system_prompt.md`, `config/coach_principles.md`, `config/user_profile.md`,
`config/training_preferences.md`, and `config/coach_settings.yaml`.

## Commands

All commands are used via Discord DM (or any channel the bot is in), prefixed with `!`. See
[`docs/bot_guide.md`](docs/bot_guide.md) for the full reference, including image upload keywords.

| Command | What it does | Example |
|---|---|---|
| `!checkin` | Accountability nudge — surfaces today's plan and any open commitments | `!checkin` |
| `!workout` | Logs a completed workout from free text or one or more Arrow/Apple Fitness screenshots; typed exercise lists get parsed into structured sets, and any set that beats your prior best for that exercise is flagged as a new PR | `!workout bicep curl: 30lbs x10, 35lbs x5` |
| `!cardio` | Logs cardio as minutes + modality | `!cardio 35 incline walk` |
| `!nutrition` | Logs carbs, fat, protein, and calories **remaining** (MyFitnessPal's "Nutrients Remaining" numbers), in that order | `!nutrition 129g -1g 90g 814cals` |
| `!sleep` | Logs sleep as plain minutes or an `Xh Ym` duration | `!sleep 6h38m` |
| `!commit` | Saves an accountability commitment (a to-do the coach follows up on) | `!commit I'll do cardio tomorrow morning` |
| `!adjust` | Logs a short-lived deviation from the default schedule for N days starting today, then the default split resumes automatically | `!adjust 2 Shift chest/back to Monday, cardio/rest Tuesday this week` |
| `!progress` | Delivers the periodic progress review, but only once it's actually due (default: every 30 days) — analytics stay locked between review periods by design | `!progress` |
| `!recent` | Lists the last few logged workouts, cardio, nutrition, and sleep entries straight from the database (default 5 each, max 20) — a raw sanity check that logging is working, not an analytics view, so it's available even while `!progress` is locked | `!recent 10` |

## Test

```bash
pytest
```

Analytics are deterministic Python functions. The LLM only receives already-computed metrics for
explanation and should never calculate analytics itself.
