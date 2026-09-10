from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from hypothesis import given
from hypothesis import strategies as st

from fitness_coach.analytics.adherence import completion_rate, current_streak, missed_days
from fitness_coach.analytics.cardio import summarize_cardio
from fitness_coach.analytics.measurements import summarize_measurements
from fitness_coach.analytics.nutrition import (
    average_nutrition,
    missing_nutrition_days,
    protein_goal_adherence,
)
from fitness_coach.analytics.reports import build_progress_metrics
from fitness_coach.analytics.strength import (
    best_set_among,
    best_weight_by_exercise,
    find_new_personal_records,
    judge_against_baseline,
    next_tracked_weight,
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


@dataclass
class MeasurementFixture:
    measured_at: datetime
    body_weight_lb: float | None = None
    waist_inches: float | None = None
    body_fat_percent: float | None = None
    progress_photo_path: str | None = None


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
    averages = average_nutrition(events, dt(2026, 7, 1), dt(2026, 7, 2))
    assert averages.days_logged == 2
    assert averages.calories == 2100
    assert protein_goal_adherence(events, 150, dt(2026, 7, 1), dt(2026, 7, 2)) == 0.5


def test_nutrition_averages_and_protein_goal_treat_unlogged_days_as_below_goal() -> None:
    events = [NutritionFixture(dt(2026, 7, 1), 2200, 150, 250, 70)]
    averages = average_nutrition(events, dt(2026, 7, 1), dt(2026, 7, 3))
    assert averages.days_logged == 1
    assert averages.calories == 2200 / 3
    assert protein_goal_adherence(events, 150, dt(2026, 7, 1), dt(2026, 7, 3)) == 1 / 3


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


def test_strength_volume_and_best_weight_handles_nested_sets_shape() -> None:
    # This is the shape real workouts are actually logged in (vision extraction and
    # PR detection both use it) - sets is a list of {"weight", "reps"} objects, not a
    # plain count. Regression test: this used to raise TypeError (float() argument
    # must be a string or a real number, not 'list') as soon as any real workout
    # history reached exercise_volume/best_weight_by_exercise, which only happens
    # once a 30-day progress review actually fires.
    workout = WorkoutFixture(
        dt(2026, 7, 1),
        exercises=[
            {
                "name": "Bench Press",
                "sets": [{"weight": 135, "reps": 8}, {"weight": 135, "reps": 8}],
            }
        ],
    )
    assert total_workout_volume(workout) == 2160
    assert best_weight_by_exercise([workout]) == {"bench press": 135}


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


def test_best_set_among_ranks_by_estimated_one_rep_max() -> None:
    exercises = [
        {
            "name": "Bench Press",
            "sets": [{"weight": 135, "reps": 8}, {"weight": 145, "reps": 3}],
        }
    ]
    assert best_set_among(exercises) == {"bench press": (135, 8)}


def test_judge_against_baseline_boundaries() -> None:
    # +/-5% band around a 100lb baseline: 95-105 is "good".
    assert judge_against_baseline(logged_weight=94.9, baseline_weight=100, max_weight=None) == (
        "under",
        False,
    )
    assert judge_against_baseline(logged_weight=95, baseline_weight=100, max_weight=None) == (
        "good",
        False,
    )
    assert judge_against_baseline(logged_weight=105, baseline_weight=100, max_weight=None) == (
        "good",
        False,
    )
    assert judge_against_baseline(logged_weight=105.1, baseline_weight=100, max_weight=None) == (
        "over",
        False,
    )


def test_judge_against_baseline_flags_near_max() -> None:
    verdict, near_max = judge_against_baseline(
        logged_weight=95, baseline_weight=100, max_weight=100
    )
    assert verdict == "good"
    assert near_max is True

    _, near_max_far = judge_against_baseline(logged_weight=100, baseline_weight=100, max_weight=200)
    assert near_max_far is False


def test_next_tracked_weight_increments_on_match_and_resets_on_change() -> None:
    tracked, streak = next_tracked_weight(
        current_tracked_weight=145, current_streak=2, logged_weight=145
    )
    assert (tracked, streak) == (145, 3)

    tracked, streak = next_tracked_weight(
        current_tracked_weight=145, current_streak=4, logged_weight=150
    )
    assert (tracked, streak) == (150, 1)

    tracked, streak = next_tracked_weight(
        current_tracked_weight=None, current_streak=0, logged_weight=135
    )
    assert (tracked, streak) == (135, 1)


def test_summarize_measurements_uses_first_and_last_logged_value() -> None:
    events = [
        MeasurementFixture(dt(2026, 7, 1), body_weight_lb=180, progress_photo_path="a.jpg"),
        MeasurementFixture(dt(2026, 7, 15), waist_inches=34),
        MeasurementFixture(dt(2026, 7, 30), body_weight_lb=178, progress_photo_path="b.jpg"),
    ]
    summary = summarize_measurements(events)
    assert summary.entries_logged == 3
    assert summary.photos_logged == 2
    assert summary.body_weight_lb is not None
    assert summary.body_weight_lb.first_value == 180
    assert summary.body_weight_lb.last_value == 178
    assert summary.body_weight_lb.change == -2
    # Only one waist entry logged - not enough for a trend.
    assert summary.waist_inches is None
    assert summary.body_fat_percent is None


def test_progress_metrics_are_deterministic() -> None:
    # Exercises use the real nested-sets shape (not an empty list) so this also
    # regression-tests build_progress_metrics -> volume_by_exercise against the
    # shape real logged workouts actually have - see
    # test_strength_volume_and_best_weight_handles_nested_sets_shape.
    metrics = build_progress_metrics(
        workouts=[
            WorkoutFixture(
                dt(2026, 7, 1),
                exercises=[{"name": "Bench Press", "sets": [{"weight": 135, "reps": 8}]}],
            )
        ],
        cardio_events=[CardioFixture(dt(2026, 7, 2), 20)],
        nutrition_events=[
            # Meets both the strict goal (150) and the lower adherence threshold (145).
            NutritionFixture(dt(2026, 7, 1), 2000, 150, 200, 70),
            # Meets only the adherence threshold, not the strict goal.
            NutritionFixture(dt(2026, 7, 2), 2000, 146, 200, 70),
        ],
        measurements=[
            MeasurementFixture(dt(2026, 7, 1), body_weight_lb=180),
            MeasurementFixture(dt(2026, 7, 3), body_weight_lb=179),
        ],
        start=dt(2026, 7, 1),
        end=dt(2026, 7, 3),
        today=date(2026, 7, 3),
        protein_goal_g=150,
        protein_adherence_threshold_g=145,
    )
    assert metrics["workouts"]["sessions"] == 1
    assert metrics["workouts"]["volume_by_exercise"] == {"bench press": 1080}
    assert metrics["cardio"]["total_minutes"] == 20
    assert metrics["nutrition"]["protein_goal_adherence"] == 1 / 3
    assert metrics["nutrition"]["protein_adherence_rate"] == 2 / 3
    assert metrics["measurements"]["body_weight_lb"]["change"] == -1


@given(st.lists(st.dates(min_value=date(2020, 1, 1), max_value=date(2020, 1, 31))))
def test_completion_rate_is_bounded(days: list[date]) -> None:
    rate = completion_rate(set(days), date(2020, 1, 1), date(2020, 1, 31))
    assert 0 <= rate <= 1
