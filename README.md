# Fitness Accountability Coach

A Discord-first AI fitness accountability coach built around structured memory, event-driven
SQLite persistence, deterministic analytics, and a thin Discord adapter.

This is not a replacement for MyFitnessPal, Hevy, Apple Health, Garmin, or ChatGPT. It is a
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

Fill in `.env` with your Discord bot token, Discord IDs, OpenAI key, and database URL.

## Run

```bash
fitness-coach-api
fitness-coach-bot
```

The first startup initializes the SQLite schema. Runtime coaching behavior is loaded from
`config/system_prompt.md`, `config/coach_principles.md`, `config/user_profile.md`,
`config/training_preferences.md`, and `config/coach_settings.yaml`.

## Test

```bash
pytest
```

Analytics are deterministic Python functions. The LLM only receives already-computed metrics for
explanation and should never calculate analytics itself.
