"""Deterministic 30-day review metrics: what the narrative is allowed to talk about."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from fitness_coach.analytics.reports import build_progress_metrics

START = datetime(2026, 10, 6, tzinfo=UTC)


def day(offset: int) -> datetime:
    return START + timedelta(days=offset)


@dataclass
class Workout:
    occurred_at: datetime
    exercises: list[dict[str, Any]]
    workout_type: str = "Upper"


@dataclass
class Nutrition:
    logged_for: datetime
    protein_g: float
    calories: int = 2300
    carbs_g: float = 300
    fat_g: float = 55


@dataclass
class Sleep:
    logged_for: datetime
    time_asleep_minutes: int | None
    regularity_percent: float | None = None
    sleep_latency_minutes: int | None = None
    wake_up_mood: str | None = None


@dataclass
class Cardio:
    occurred_at: datetime = field(default_factory=lambda: START)
    duration_minutes: int = 20
    modality: str = "walk"
    distance_miles: float | None = None


def _sets(*pairs: tuple[float, int]) -> list[dict[str, float]]:
    return [{"weight": w, "reps": r} for w, r in pairs]


def _metrics(**overrides: Any) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "workouts": [],
        "cardio_events": [],
        "nutrition_events": [],
        "measurements": [],
        "start": START,
        "end": day(9),  # a 10-day window keeps the arithmetic obvious
        "today": date(2026, 10, 15),
        "protein_goal_g": 160,
        "protein_adherence_threshold_g": 145,
    }
    arguments.update(overrides)
    return build_progress_metrics(**arguments)


def test_nutrition_reports_coverage_and_logged_day_figures_next_to_calendar_rates() -> None:
    nutrition = [
        Nutrition(day(0), 170),
        Nutrition(day(1), 150),
        Nutrition(day(2), 100),
        Nutrition(day(3), 165),
    ]

    block = _metrics(nutrition_events=nutrition)["nutrition"]

    assert block["days_logged"] == 4
    assert block["missing_days"] == 6
    assert block["coverage"] == 0.4
    assert block["averages_logged_days"]["protein_g"] == 146.25
    # Deliberate earlier policy kept: unlogged days still count against the calendar rates.
    assert block["averages"]["protein_g"] == 58.5
    assert (block["protein_goal_met_days"], block["protein_near_goal_days"]) == (2, 3)
    assert (block["protein_goal_met_rate"], block["protein_near_goal_rate"]) == (0.2, 0.3)


def test_macro_goal_days_count_each_goal_separately() -> None:
    nutrition = [
        Nutrition(day(0), 170, calories=2300, carbs_g=300, fat_g=55),  # everything met
        Nutrition(day(1), 120, calories=1500, carbs_g=300, fat_g=90),  # only carbs met
    ]
    goals = {"calories": 2335, "protein": 160, "carbs": 300, "fat": 55}

    macros = _metrics(nutrition_events=nutrition, macro_goals=goals)["nutrition"]["macro_goal_days"]

    assert macros == {"calories": 1, "protein": 1, "carbs": 2, "fat": 1}


def test_sleep_block_counts_only_nights_with_a_duration() -> None:
    sleep = [Sleep(day(0), 450), Sleep(day(1), 330), Sleep(day(2), None), Sleep(day(3), 480)]

    block = _metrics(sleep_events=sleep)["sleep"]

    assert block["nights_logged"] == 3
    assert block["logging_rate"] == 0.3
    assert block["average_time_asleep_minutes"] == 420
    assert block["nights_under_six_hours"] == 1


def test_sleep_block_with_no_logs_does_not_invent_numbers() -> None:
    block = _metrics()["sleep"]

    assert block == {
        "nights_logged": 0,
        "logging_rate": 0.0,
        "average_time_asleep_minutes": None,
        "average_regularity_percent": None,
        "average_sleep_latency_minutes": None,
        "nights_under_six_hours": 0,
        "mood_counts": {},
    }


def test_working_volume_replaces_raw_volume_so_warmups_do_not_inflate_it() -> None:
    workouts = [
        Workout(
            day(1),
            [{"name": "Bench Press", "sets": _sets((45, 10), (60, 8), (100, 5), (90, 6))}],
        )
    ]

    volume = _metrics(workouts=workouts)["workouts"]["volume_by_exercise"]

    assert volume == {"bench press": 100 * 5 + 90 * 6}


def test_highlights_name_real_progress_and_stalls_and_ignore_warmups() -> None:
    press = [(day(0), (60, 8)), (day(3), (65, 8)), (day(6), (70, 8))]
    pulldown = [(day(0), (100, 8)), (day(2), (100, 8)), (day(4), (100, 8)), (day(6), (100, 8))]
    workouts = []
    for when, top in press:
        # A 20-rep warm-up must not be mistaken for the top set.
        sets = _sets((20, 20), top)
        workouts.append(Workout(when, [{"name": "Dumbbell Press", "sets": sets}]))
    for when, top in pulldown:
        workouts.append(Workout(when, [{"name": "Lat Pulldown", "sets": _sets(top)}]))

    highlights = _metrics(workouts=workouts)["workouts"]["highlights"]

    assert highlights["progress"] == [
        {
            "exercise": "Dumbbell Press",
            "from": {"weight": 60, "reps": 8},
            "to": {"weight": 70, "reps": 8},
        }
    ]
    assert highlights["stalled"] == [{"exercise": "Lat Pulldown", "sessions": 4}]


def test_highlights_are_capped_and_a_single_session_is_never_a_trend() -> None:
    workouts = [Workout(day(0), [{"name": f"Lift {n}", "sets": _sets((50, 8))}]) for n in range(6)]
    for n in range(5):
        workouts.append(Workout(day(5), [{"name": f"Lift {n}", "sets": _sets((60, 8))}]))

    highlights = _metrics(workouts=workouts)["workouts"]["highlights"]

    assert len(highlights["progress"]) == 3
    assert highlights["stalled"] == []


def test_empty_period_produces_empty_highlights() -> None:
    assert _metrics()["workouts"]["highlights"] == {"progress": [], "stalled": []}


def test_period_reports_its_length_so_the_narrative_can_say_14_of_31_days() -> None:
    assert _metrics()["period"]["days"] == 10
