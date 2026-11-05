"""The 30-day review: assembled metrics and the instructions the narrative is written under."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from fitness_coach.coach.factory import ServiceFactory
from fitness_coach.coach.openai_client import OpenAIResult
from fitness_coach.config.settings import AppSettings, CoachSettings
from fitness_coach.database.schemas import NutritionLog, SleepLog, WorkoutLog

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)


@pytest.fixture
def factory(tmp_path: Path) -> ServiceFactory:
    result = ServiceFactory(
        AppSettings(
            database_url=f"sqlite:///{tmp_path / 'coach.db'}",
            config_dir=Path("config"),
            uploads_dir=tmp_path / "uploads",
        ),
        CoachSettings(preferred_model="test-model"),
    )
    yield result
    result.engine.dispose()


class _CapturingOpenAI:
    def __init__(self) -> None:
        self.user_message = ""

    def respond(self, *, system_prompt: str, user_message: str, **_: object) -> OpenAIResult:
        self.user_message = user_message
        return OpenAIResult(text="stub review", metadata={})


def _seed(coach, user_id: str) -> None:
    for week, weight in enumerate((60, 65, 70)):
        coach.log_workout(
            user_id,
            WorkoutLog(
                occurred_at=NOW - timedelta(days=21 - 7 * week),
                workout_type="Upper",
                exercises=[
                    {
                        "name": "Dumbbell Press",
                        "sets": [{"weight": 20, "reps": 20}, {"weight": weight, "reps": 8}],
                    }
                ],
            ),
        )
    coach.log_sleep(
        user_id,
        SleepLog(logged_for=NOW - timedelta(days=2), time_asleep_minutes=385),
    )
    coach.log_nutrition(
        user_id,
        NutritionLog(
            logged_for=NOW - timedelta(days=1), calories=2300, protein_g=170, carbs_g=300, fat_g=55
        ),
    )


def test_review_metrics_cover_sleep_highlights_and_macro_goals_from_settings(
    factory: ServiceFactory,
) -> None:
    with factory.session() as session:
        coach = factory.coach_service(session)
        user = coach.get_user("123")
        _seed(coach, user.id)

        metrics = coach.build_review_metrics(user.id, NOW)

    assert metrics["sleep"]["nights_logged"] == 1
    assert metrics["sleep"]["average_time_asleep_minutes"] == 385
    assert metrics["workouts"]["highlights"]["progress"][0]["exercise"] == "Dumbbell Press"
    assert metrics["workouts"]["volume_by_exercise"] == {"dumbbell press": 60 * 8 + 65 * 8 + 70 * 8}
    assert metrics["nutrition"]["macro_goal_days"]["protein"] == 1
    assert metrics["nutrition"]["coverage"] > 0


def test_review_prompt_asks_for_takeaways_in_a_bounded_length_with_json_metrics(
    factory: ServiceFactory,
) -> None:
    with factory.session() as session:
        coach = factory.coach_service(session)
        user = coach.get_user("123")
        _seed(coach, user.id)
        metrics = coach.build_review_metrics(user.id, NOW)
        stub = _CapturingOpenAI()
        coach.openai = stub  # type: ignore[assignment]

        review = coach.maybe_generate_review(user.id, metrics, NOW)

    assert review is not None and review.narrative == "stub review"
    message = stub.user_message
    metrics_json = message[message.index("{") :]
    assert json.loads(metrics_json)["sleep"]["nights_logged"] == 1  # real JSON, not a Python repr
    lowered = message.lower()
    assert "300 words" in lowered
    assert "1-3" in lowered and "takeaway" in lowered
    assert "interpretation" in lowered  # named as something NOT to write
    assert "never invent a problem" in lowered or "do not invent a problem" in lowered
    assert "coverage" in lowered
    assert "failure" in lowered
