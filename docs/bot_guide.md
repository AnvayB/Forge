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
| `!workout` | `!workout Chest/back/shoulders, felt strong` or attach one or more Arrow/Apple Fitness screenshots | Logs a completed workout — free text and typed exercise lists (e.g. `bicep curl: 30lbs x10, 35lbs x5`) get parsed into structured sets, same as a screenshot would. If a set beats every prior set you've logged for that exercise (by estimated one-rep max), the confirmation calls it out as a new PR — a single-event fact, not locked analytics |
| `!setfix` | `!setfix shoulder press 1-2 warmup` | Re-tags sets in your **latest** workout (`warmup`, `working` or `backoff`; `w`, `top` and `bo` also work). Set numbers count in the order shown, with `×N` counting as N sets. Only the label changes — logged weights and reps are never touched. Bad input gets a one-line reply and changes nothing |
| `!cardio` | `!cardio 35 incline walk` | Logs cardio — minutes, then modality |
| `!nutrition` | `!nutrition 129g -1g 90g 814cals` | Logs carbs, fat, protein, calories **remaining** (MyFitnessPal's "Nutrients Remaining" numbers) — in that order; units like `g`/`cals` are optional, plain numbers work too |
| `!sleep` | `!sleep 6h38m` or `!sleep 398` | Logs sleep — plain minutes or an `Xh Ym` duration |
| `!commit` | `!commit I'll do cardio tomorrow morning` | Saves an accountability commitment (a to-do to follow up on) |
| `!adjust` | `!adjust 2 Shift chest/back to Monday, cardio/rest Tuesday this week` | Logs a short-lived deviation from the default schedule — applies for the given number of days starting today, then the default split resumes automatically. Use this whenever the coach agrees to a schedule change in chat, since it won't otherwise remember |
| `!progress` | `!progress` | Delivers your periodic progress review, but only once it's actually due (default: every 30 days) — analytics stay locked between review periods by design |
| `!recent` | `!recent` or `!recent 10` | Lists your last few logged workouts, cardio, nutrition, and sleep entries straight from the database (default 5 each, max 20) — a raw sanity check that logging is working, not an analytics view, so it stays available even while `!progress` is locked |
| `!setbaseline` | `!setbaseline Bench Press 135 185` | Sets the baseline weight (and optional max) that each logged set of that exercise is judged against |
| `!baselines` | `!baselines` | Lists your configured baselines |
| `!lastreview` | `!lastreview` | Re-sends your most recent progress review |
| `!route` | `!route why is my bench stuck` | Shows how the router classifies a message (route, tools, research allowed) — a diagnostic, nothing is sent to the model |

## Image uploads

**Warm-ups and back-offs.** You don't have to tag anything: the bot infers them from the shape of the exercise (clearly lighter sets before your heaviest set are warm-ups; later sets are back-offs). Warm-ups show in parentheses and lighter back-offs after an arrow, e.g. `Incline Press: (20lbs x10, 25lbs x8) 40lbs x5 → 35lbs x8 ×2`. Warm-ups are left out of working volume, "top set" comparisons and weight suggestions, but a set that beats your best is still a PR whatever it's labeled. When typing a log you can tag a set inline (`curl: 20x10 w, 30x8, 35x5, 30x8 bo`), or fix it afterwards with `!setfix`. Matching right/left dumbbell rows from Arrow are logged as one set.

**Weight suggestions** only appear as a single `Next time (...)` line when the data earns one: the top set reached the top of the exercise's rep range in 2 sessions in a row (add weight), or reps stayed under the range twice (ease off). Rep ranges have defaults by exercise type and can be overridden under `rep_targets` in `config/coach_settings.yaml`. Suggestions are optional guidance — leave 1–3 reps in reserve — and nothing changes your recorded weights.

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

Anything else you type (no `!`, no attachment) goes through the response router and then to the coach — e.g. **"what's my workout today?"**, **"why has my bench not moved in a month?"**, **"what's a good second tricep exercise for elbow issues?"**. The router decides which of your data the coach may look up for that message and whether it may research; see [`intelligence_guide.md`](intelligence_guide.md). `!route <message>` shows the decision without sending anything to the model.

Note: questions about your cumulative stats (streaks, averages, adherence, monthly trends) are redirected to your next scheduled review — this is intentional. Questions about a single lift ("how's my bench going?") are answered from your logged sets.
