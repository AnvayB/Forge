Build a production-quality personal AI Fitness Accountability Coach.

========================
PROJECT VISION
========================

This project is NOT a fitness tracker.

It is NOT a replacement for:

- MyFitnessPal
- Hevy
- Apple Health
- Garmin
- ChatGPT

Instead, it acts as an AI accountability partner that helps me remain consistent with my long-term fitness goals.

The Discord bot should reduce decision fatigue while encouraging consistency.

The AI should think like a long-term evidence-based personal trainer.

========================
PRIMARY RESPONSIBILITIES
========================

The bot has four major responsibilities:

1. Accountability
    - Daily check-ins
    - Remember commitments
    - Encourage consistency
    - Follow up after missed workouts
    - Verify workout proof through uploaded screenshots

2. Workout Planning

    Recommend today's workout based on:

    - previous workouts
    - recovery
    - injuries
    - available time
    - preferred split
    - cardio history
    - progressive overload

3. Coaching

    Answer lightweight fitness questions.

    Examples:

    - workout advice
    - nutrition advice
    - recovery
    - supplements
    - injuries

    Do NOT build an academic research assistant.

    The user already uses ChatGPT for deep scientific discussions.

4. Habit Tracking

    Track only:

    - workout completion
    - cardio completion
    - commitments
    - nutrition summaries
    - nightly sleep summaries

    Do NOT replace dedicated tracking apps.

========================
NUTRITION
========================

Each evening the bot should ask for:

Calories

Protein

Carbs

Fat

The user manually copies these values from MyFitnessPal.

The bot stores only these totals.

No meal logging.

========================
SLEEP
========================

Each morning the user may upload a screenshot of the prior night's sleep summary
from their sleep-tracking app.

The bot stores only extracted structured data:

- time asleep
- sleep stages (light, deep, REM)
- regularity
- sleep latency ("asleep after")
- wake-up mood

No sleep screenshots are retained after processing.

Sleep data feeds into recovery-aware coaching, alongside nutrition and training load.

========================
WORKOUT PROOF
========================

Users upload screenshots.

Examples:

- Hevy
- Apple Fitness
- treadmill
- Garmin
- gym selfie (optional)

The AI should analyze the image.

If confidence is low, ask follow-up questions instead of hallucinating.

========================
ANALYTICS
========================

The user intentionally wants analytics hidden.

The AI may see analytics.

The user should only receive detailed reviews every configurable interval.

Default:

30 days.

The AI should refuse requests to reveal cumulative analytics outside the review period unless a significant health, recovery, or adherence issue requires earlier intervention.

========================
TECH STACK
========================

Python 3.12

discord.py

FastAPI

OpenAI Responses API

SQLite

SQLAlchemy

Pydantic

python-dotenv

APScheduler

Pillow

pytest

Hypothesis

========================
ARCHITECTURE
========================

The Discord bot should remain extremely thin.

Business logic belongs elsewhere.

Create clean modules.

planner/

analytics/

memory/

vision/

database/

scheduler/

coach/

========================
PROMPT ARCHITECTURE
========================

Create the following files.

config/

system_prompt.md

coach_principles.md

user_profile.md

training_preferences.md

coach_settings.yaml

The application should load all of these files automatically.

Implement a PromptBuilder class.

PromptBuilder should assemble a final system prompt in this order:

1. system_prompt.md
2. coach_principles.md
3. user_profile.md
4. training_preferences.md
5. dynamically generated context from SQLite

Do NOT concatenate files manually throughout the codebase.

Use PromptBuilder everywhere.

========================
CONTENT OF EACH FILE
========================

`system_prompt.md`

Contains:

- AI role
- coaching style
- evidence standards
- accountability philosophy
- communication style
- image analysis expectations

`coach_principles.md`

Contains:

- consistency over perfection
- behavior over optimization
- never shame the user
- long-term thinking
- reduce decision fatigue
- do not replace existing apps
- analytics locking philosophy
- when to intervene early
- prioritize sustainability
- explain reasoning

`user_profile.md`

Contains:

- user goals
- injury history
- nutrition preferences
- accountability preferences
- cardio preferences
- preferred coaching style
- current philosophy
- existing tracking apps

`training_preferences.md`

Contains:

- preferred split
- favorite exercises
- exercises to avoid
- available equipment
- preferred workout duration
- progression philosophy
- warm-up philosophy
- cardio preferences

`coach_settings.yaml`

Contains configurable values such as:

timezone

daily reminder time

review interval

protein goal

default cardio goal

preferred AI model

analytics lock enabled

response verbosity

measurement reminder interval

progress photo reminder interval

========================
ANALYTICS
========================

The LLM must NEVER calculate analytics.

Create deterministic Python modules.

analytics/

cardio.py

nutrition.py

strength.py

adherence.py

reports.py

The LLM only explains analytics.

========================
TESTING
========================

Analytics must achieve >90% unit test coverage.

Use:

pytest

Hypothesis

Create realistic fixtures.

Test:

- streaks
- averages
- monthly reports
- missing data
- leap years
- edge cases

========================
CODE QUALITY
========================

Use:

Strong typing

Dependency injection

Structured logging

Pydantic models

Docstrings

Comprehensive README

Generate clean, maintainable code suitable for long-term development.