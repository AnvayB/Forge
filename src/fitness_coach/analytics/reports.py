"""Deterministic progress report assembly."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict
from datetime import date, datetime
from typing import Any

from fitness_coach.analytics.adherence import completion_rate, current_streak, unique_event_dates
from fitness_coach.analytics.cardio import CardioLike, summarize_cardio
from fitness_coach.analytics.nutrition import (
    NutritionLike,
    average_nutrition,
    missing_nutrition_days,
    protein_goal_adherence,
)
from fitness_coach.analytics.strength import WorkoutLike, sessions_by_type, volume_by_exercise


def build_progress_metrics(
    *,
    workouts: Iterable[WorkoutLike],
    cardio_events: Iterable[CardioLike],
    nutrition_events: Iterable[NutritionLike],
    start: datetime,
    end: datetime,
    today: date,
    protein_goal_g: float,
) -> dict[str, Any]:
    """Build deterministic review metrics from event data."""

    workout_list = list(workouts)
    cardio_list = list(cardio_events)
    nutrition_list = list(nutrition_events)
    active_dates = unique_event_dates([*workout_list, *cardio_list])
    nutrition_averages = average_nutrition(nutrition_list, start, end)

    return {
        "period": {
            "start": start.isoformat(),
            "end": end.isoformat(),
        },
        "adherence": {
            "current_activity_streak_days": current_streak(active_dates, today),
            "activity_completion_rate": completion_rate(active_dates, start.date(), end.date()),
        },
        "workouts": {
            "sessions": len(workout_list),
            "sessions_by_type": sessions_by_type(workout_list),
            "volume_by_exercise": volume_by_exercise(workout_list),
        },
        "cardio": asdict(summarize_cardio(cardio_list)),
        "nutrition": {
            "averages": asdict(nutrition_averages),
            "protein_goal_adherence": protein_goal_adherence(
                nutrition_list, protein_goal_g, start, end
            ),
            "missing_days": missing_nutrition_days(nutrition_list, start, end),
        },
    }
