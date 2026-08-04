# Fitness Coach Bot — Reference Guide

Interact via DM. Everything below works the same in a DM as it would in a server channel.

## Automatic daily messages

| Time | What it does |
|---|---|
| 10:00 AM | DMs you asking how you slept (screenshot or manual hours/minutes), and tells you today's workout |
| 10:00 PM | DMs you asking for today's macros |

These only fire once the bot has captured your Discord user ID — send it at least one message first (any command or question) if you're setting this up for the first time.

## Commands

| Command | Example | What it does |
|---|---|---|
| `!checkin` | `!checkin` | Accountability nudge — your plan for today, open commitments |
| `!workout` | `!workout Chest/back/shoulders, felt strong` | Logs a completed workout from free text |
| `!cardio` | `!cardio 35 incline walk` | Logs cardio — minutes, then modality |
| `!nutrition` | `!nutrition 2400 180 250 80` | Logs calories, protein, carbs, fat — in that order |
| `!sleep` | `!sleep 341 felt rested` | Logs minutes asleep, optional note |
| `!commit` | `!commit I'll do cardio tomorrow morning` | Saves an accountability commitment |

## Image uploads

Attach an image and **include a keyword** in your message — classification is keyword-based, not visual.

| Keyword to include | Image type |
|---|---|
| `workout` (or nothing — it's the default) | Workout proof (Hevy, etc.) |
| `nutrition`, `myfitnesspal`, `macro` | Nutrition screenshot |
| `cardio`, `treadmill`, `garmin`, `run` | Cardio screenshot |
| `sleep` | Sleep summary (SleepCycle, etc.) |
| `progress`, `photo` | Progress photo (the only image type that's retained — everything else is deleted after processing) |

## Free-text questions

Anything else you type (no `!`, no attachment) goes straight to the coach — e.g. **"what's my workout today?"**, **"what's a good second tricep exercise for elbow issues?"**.

Note: questions containing analytics-sounding terms (`streak`, `average`, `trend`, `monthly`, `progress report`, `analytics`) are blocked until your next scheduled review — this is intentional, not a bug.
