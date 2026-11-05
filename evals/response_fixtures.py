"""Fixed workout data for before/after response-quality comparisons.

The "arms day" below mirrors a real Arrow screenshot as the bot logged it: Dumbbell rows
for the right and left hand arrive as two identical sets (``x2`` in the reply), and warm-up
sets are not marked. Running the same fixture before and after a change makes reply length
and content directly comparable without asserting exact LLM text.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fitness_coach.coach.factory import ServiceFactory
from fitness_coach.database.schemas import WorkoutLog
from fitness_coach.vision.processor import ImageKind, VisionExtraction

BASELINE_PATH = Path(__file__).parent / "results" / "baseline_response_quality.json"


def _sets(*rows: tuple[float, int, int]) -> list[dict[str, Any]]:
    """Expand ``(weight, reps, count)`` into one dict per set."""

    return [{"weight": weight, "reps": reps} for weight, reps, count in rows for _ in range(count)]


ARMS_DAY_EXERCISES: list[dict[str, Any]] = [
    {
        "name": "Incline Dumbbell Curl",
        "sets": _sets((20, 8, 2), (20, 10, 2), (22.5, 8, 4)),
    },
    {
        "name": "Cable Tricep Pushdown",
        "sets": _sets((80, 15, 1), (100, 10, 1), (110, 10, 1), (120, 7, 1)),
    },
    {
        "name": "Dumbbell Shoulder Press",
        "sets": _sets((25, 10, 2), (27.5, 8, 2), (32.5, 8, 2), (40, 8, 2), (32.5, 10, 2)),
    },
    {
        "name": "Dumbbell Hammer Curl",
        "sets": _sets((25, 8, 2), (27.5, 7, 2), (27.5, 9, 2), (27.5, 8, 2)),
    },
]

# Two earlier sessions so quality comparison and PR detection have history to work with.
PRIOR_SESSIONS: list[tuple[datetime, list[dict[str, Any]]]] = [
    (
        datetime(2026, 10, 14, tzinfo=UTC),
        [
            {"name": "Dumbbell Shoulder Press", "sets": _sets((30, 8, 2), (35, 8, 2))},
            {"name": "Dumbbell Hammer Curl", "sets": _sets((25, 8, 2), (30, 5, 2))},
        ],
    ),
    (
        datetime(2026, 10, 21, tzinfo=UTC),
        [
            {"name": "Dumbbell Shoulder Press", "sets": _sets((30, 8, 2), (35, 8, 2))},
            {"name": "Dumbbell Hammer Curl", "sets": _sets((25, 8, 2), (27.5, 8, 2))},
        ],
    ),
]


def seed_prior_sessions(factory: ServiceFactory, user_id: str) -> None:
    with factory.session() as session:
        coach = factory.coach_service(session)
        for occurred_at, exercises in PRIOR_SESSIONS:
            coach.log_workout(
                user_id,
                WorkoutLog(occurred_at=occurred_at, workout_type="Arms", exercises=exercises),
            )


def arms_day_extraction(exercises: list[dict[str, Any]] | None = None) -> VisionExtraction:
    return VisionExtraction(
        kind=ImageKind.WORKOUT_SCREENSHOT,
        confidence=0.95,
        needs_clarification=False,
        facts={"workout_type": "Arms", "exercises": exercises or ARMS_DAY_EXERCISES},
        retained_path=None,
    )


def workout_reply(factory: ServiceFactory, exercises: list[dict[str, Any]] | None = None) -> str:
    """Log prior history, then today's arms day through the screenshot path; return the reply."""

    with factory.session() as session:
        user = factory.coach_service(session).get_user("123")
        user_id = user.id
    seed_prior_sessions(factory, user_id)
    with factory.session() as session:
        coach = factory.coach_service(session)
        return coach.store_vision_extraction(user_id, arms_day_extraction(exercises)).message


def reply_stats(text: str) -> dict[str, int]:
    lines = text.splitlines()
    return {
        "words": len(text.split()),
        "lines": len(lines),
        "max_line_chars": max((len(line) for line in lines), default=0),
    }


def load_baseline() -> dict[str, Any]:
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
