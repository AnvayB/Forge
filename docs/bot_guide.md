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
| `!workout` | `!workout Chest/back/shoulders, felt strong` or attach one or more Arrow/Apple Fitness screenshots | Logs a completed workout — free text and typed exercise lists (e.g. `bicep curl: 30lbs x10, 35lbs x5`) get parsed into structured sets, same as a screenshot would |
| `!cardio` | `!cardio 35 incline walk` | Logs cardio — minutes, then modality |
| `!nutrition` | `!nutrition 129g -1g 90g 814cals` | Logs carbs, fat, protein, calories **remaining** (MyFitnessPal's "Nutrients Remaining" numbers) — in that order; units like `g`/`cals` are optional, plain numbers work too |
| `!sleep` | `!sleep 6h38m` or `!sleep 398` | Logs sleep — plain minutes or an `Xh Ym` duration |
| `!commit` | `!commit I'll do cardio tomorrow morning` | Saves an accountability commitment (a to-do to follow up on) |
| `!adjust` | `!adjust 2 Shift chest/back to Monday, cardio/rest Tuesday this week` | Logs a short-lived deviation from the default schedule — applies for the given number of days starting today, then the default split resumes automatically. Use this whenever the coach agrees to a schedule change in chat, since it won't otherwise remember |
| `!progress` | `!progress` | Delivers your periodic progress review, but only once it's actually due (default: every 30 days) — analytics stay locked between review periods by design |
| `!recent` | `!recent` or `!recent 10` | Lists your last few logged workouts, cardio, nutrition, and sleep entries straight from the database (default 5 each, max 20) — a raw sanity check that logging is working, not an analytics view, so it stays available even while `!progress` is locked |

## Image uploads

You can attach images directly to `!workout` (multiple screenshots — e.g. Arrow + Apple Fitness — get merged into one workout entry). For any other image type, or if you're not using a command, just attach the image with a plain message: a keyword in your message or filename picks the type, and if none matches, the bot asks the vision model to classify the screenshot itself instead of guessing.

| Keyword to include | Image type |
|---|---|
| `workout`, `arrow`, `strength`, `lift` | Workout proof (Arrow, Apple Fitness, etc.) |
| `nutrition`, `myfitnesspal`, `macro` | Nutrition screenshot |
| `cardio`, `treadmill`, `garmin`, `run` | Cardio screenshot |
| `sleep` | Sleep summary (SleepCycle, etc.) |
| `progress`, `photo` | Progress photo (the only image type that's retained — everything else is deleted after processing) |
| *(no keyword match)* | Classified automatically by the vision model |

## Free-text questions

Anything else you type (no `!`, no attachment) goes straight to the coach — e.g. **"what's my workout today?"**, **"what's a good second tricep exercise for elbow issues?"**.

Note: questions containing analytics-sounding terms (`streak`, `average`, `trend`, `monthly`, `progress report`, `analytics`) are blocked until your next scheduled review — this is intentional, not a bug.
