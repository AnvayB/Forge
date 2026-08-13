from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from hypothesis import given
from hypothesis import strategies as st

from fitness_coach.analytics.adherence import completion_rate, current_streak, missed_days
from fitness_coach.analytics.cardio import summarize_cardio
from fitness_coach.analytics.nutrition import (
    average_nutrition,
    missing_nutrition_days,
    protein_goal_adherence,
)
from fitness_coach.analytics.reports import build_progress_metrics
from fitness_coach.analytics.strength import (
    best_weight_by_exercise,
    find_new_personal_records,
    total_workout_volume,
)


@dataclass
class WorkoutFixture:
    occurred_at: datetime
    workout_type: str = "Upper"
    exercises: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class CardioFixture:
    occurred_at: datetime
    duration_minutes: int
    distance_miles: float | None = None
    modality: str = "incline treadmill"


@dataclass
class NutritionFixture:
    logged_for: datetime
    calories: int
    protein_g: float
    carbs_g: float
    fat_g: float


def dt(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=UTC)


def test_current_streak_uses_today_or_yesterday() -> None:
    dates = {date(2026, 7, 10), date(2026, 7, 11), date(2026, 7, 12)}
    assert current_streak(dates, date(2026, 7, 13)) == 3


def test_completion_rate_and_missed_days_include_range_edges() -> None:
    completed = {date(2026, 7, 1), date(2026, 7, 3)}
    assert completion_rate(completed, date(2026, 7, 1), date(2026, 7, 3)) == 2 / 3
    assert missed_days(completed, date(2026, 7, 1), date(2026, 7, 3)) == [date(2026, 7, 2)]


def test_cardio_summary_handles_empty_and_distance() -> None:
    assert summarize_cardio([]).total_minutes == 0
    summary = summarize_cardio(
        [
            CardioFixture(dt(2026, 7, 1), 20, 1.2),
            CardioFixture(dt(2026, 7, 3), 30, 2.0, "hike"),
        ]
    )
    assert summary.sessions == 2
    assert summary.total_minutes == 50
    assert summary.total_distance_miles == 3.2
    assert summary.modalities == {"incline treadmill": 1, "hike": 1}


def test_nutrition_averages_and_protein_goal() -> None:
    events = [
        NutritionFixture(dt(2026, 7, 1), 2200, 150, 250, 70),
        NutritionFixture(dt(2026, 7, 2), 2000, 120, 220, 60),
    ]
    averages = average_nutrition(events)
    assert averages.days_logged == 2
    assert averages.calories == 2100
    assert protein_goal_adherence(events, 150) == 0.5


def test_missing_nutrition_days_handles_leap_year() -> None:
    events = [NutritionFixture(dt(2024, 2, 29), 2000, 150, 200, 70)]
    assert missing_nutrition_days(events, dt(2024, 2, 28), dt(2024, 3, 1)) == 2


def test_strength_volume_and_best_weight() -> None:
    workout = WorkoutFixture(
        dt(2026, 7, 1),
        exercises=[
            {"name": "incline bench press", "sets": 3, "reps": 8, "weight_lb": 135},
            {"name": "incline bench press", "sets": 1, "reps": 6, "weight_lb": 145},
        ],
    )
    assert total_workout_volume(workout) == 4110
    assert best_weight_by_exercise([workout]) == {"incline bench press": 145}


def test_find_new_personal_records_requires_prior_history() -> None:
    history = [
        WorkoutFixture(
            dt(2026, 7, 1),
            exercises=[{"name": "Cable Lateral Raise", "sets": [{"weight": 12, "reps": 20}]}],
        )
    ]
    # No prior "Dumbbell Hammer Curl" history, so it shouldn't count as a PR yet.
    new_exercises = [
        {"name": "Cable Lateral Raise", "sets": [{"weight": 15, "reps": 20}]},
        {"name": "Dumbbell Hammer Curl", "sets": [{"weight": 30, "reps": 5}]},
    ]
    records = find_new_personal_records(history=history, new_exercises=new_exercises)
    assert [r.exercise for r in records] == ["Cable Lateral Raise"]
    assert records[0].weight == 15
    assert records[0].previous_weight == 12


def test_find_new_personal_records_ignores_sets_that_dont_beat_history() -> None:
    history = [
        WorkoutFixture(
            dt(2026, 7, 1),
            exercises=[{"name": "Bench Press", "sets": [{"weight": 135, "reps": 8}]}],
        )
    ]
    new_exercises = [{"name": "Bench Press", "sets": [{"weight": 115, "reps": 8}]}]
    assert find_new_personal_records(history=history, new_exercises=new_exercises) == []


def test_progress_metrics_are_deterministic() -> None:
    metrics = build_progress_metrics(
        workouts=[WorkoutFixture(dt(2026, 7, 1), exercises=[])],
        cardio_events=[CardioFixture(dt(2026, 7, 2), 20)],
        nutrition_events=[NutritionFixture(dt(2026, 7, 1), 2000, 150, 200, 70)],
        start=dt(2026, 7, 1),
        end=dt(2026, 7, 3),
        today=date(2026, 7, 3),
        protein_goal_g=150,
    )
    assert metrics["workouts"]["sessions"] == 1
    assert metrics["cardio"]["total_minutes"] == 20
    assert metrics["nutrition"]["protein_goal_adherence"] == 1.0


@given(st.lists(st.dates(min_value=date(2020, 1, 1), max_value=date(2020, 1, 31))))
def test_completion_rate_is_bounded(days: list[date]) -> None:
    rate = completion_rate(set(days), date(2020, 1, 1), date(2020, 1, 31))
    assert 0 <= rate <= 1
