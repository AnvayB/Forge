"""Deterministic progress report assembly."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict
from datetime import date, datetime
from typing import Any

from fitness_coach.analytics.adherence import completion_rate, current_streak, unique_event_dates
from fitness_coach.analytics.cardio import CardioLike, summarize_cardio
from fitness_coach.analytics.measurements import MeasurementLike, summarize_measurements
from fitness_coach.analytics.nutrition import (
    NutritionLike,
    average_nutrition,
    average_nutrition_logged_days,
    macro_goal_days,
    missing_nutrition_days,
    protein_goal_adherence,
    protein_goal_days,
)
from fitness_coach.analytics.progression import review_highlights, top_set_trends
from fitness_coach.analytics.sleep import SleepLike, summarize_sleep
from fitness_coach.analytics.strength import (
    WorkoutLike,
    sessions_by_type,
    working_volume_by_exercise,
)


def build_progress_metrics(
    *,
    workouts: Iterable[WorkoutLike],
    cardio_events: Iterable[CardioLike],
    nutrition_events: Iterable[NutritionLike],
    measurements: Iterable[MeasurementLike],
    start: datetime,
    end: datetime,
    today: date,
    protein_goal_g: float,
    protein_adherence_threshold_g: float,
    sleep_events: Iterable[SleepLike] = (),
    macro_goals: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Build deterministic review metrics from event data.

    ``workouts.volume_by_exercise`` counts working sets only. Nutrition rates keep the
    calendar-day policy (an unlogged day counts against the rate) and sit next to explicit
    coverage and logged-day figures so a low number can be told apart from not logging.
    """

    workout_list = list(workouts)
    cardio_list = list(cardio_events)
    nutrition_list = list(nutrition_events)
    measurement_list = list(measurements)
    sleep_list = list(sleep_events)
    active_dates = unique_event_dates([*workout_list, *cardio_list])
    nutrition_block = _nutrition_block(
        nutrition_list,
        start,
        end,
        protein_goal_g=protein_goal_g,
        protein_adherence_threshold_g=protein_adherence_threshold_g,
        macro_goals=macro_goals,
    )

    return {
        "period": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "days": (end.date() - start.date()).days + 1,
        },
        "adherence": {
            "current_activity_streak_days": current_streak(active_dates, today),
            "activity_completion_rate": completion_rate(active_dates, start.date(), end.date()),
        },
        "workouts": {
            "sessions": len(workout_list),
            "sessions_by_type": sessions_by_type(workout_list),
            "volume_by_exercise": working_volume_by_exercise(workout_list),
            "highlights": review_highlights(top_set_trends(workout_list)),
        },
        "cardio": asdict(summarize_cardio(cardio_list)),
        "nutrition": nutrition_block,
        "measurements": asdict(summarize_measurements(measurement_list)),
        "sleep": asdict(summarize_sleep(sleep_list, start, end)),
    }


def _nutrition_block(
    events: list[NutritionLike],
    start: datetime,
    end: datetime,
    *,
    protein_goal_g: float,
    protein_adherence_threshold_g: float,
    macro_goals: Mapping[str, float] | None,
) -> dict[str, Any]:
    total_days = (end.date() - start.date()).days + 1
    missing = missing_nutrition_days(events, start, end)
    logged = total_days - missing
    block: dict[str, Any] = {
        "days_logged": logged,
        "missing_days": missing,
        "coverage": logged / total_days if total_days > 0 else 0.0,
        "averages": asdict(average_nutrition(events, start, end)),
        "averages_logged_days": asdict(average_nutrition_logged_days(events, start, end)),
        "protein_goal_met_days": protein_goal_days(events, protein_goal_g, start, end),
        "protein_near_goal_days": protein_goal_days(
            events, protein_adherence_threshold_g, start, end
        ),
        "protein_goal_met_rate": protein_goal_adherence(events, protein_goal_g, start, end),
        "protein_near_goal_rate": protein_goal_adherence(
            events, protein_adherence_threshold_g, start, end
        ),
    }
    if macro_goals:
        block["macro_goal_days"] = macro_goal_days(events, macro_goals, start, end)
    return block
