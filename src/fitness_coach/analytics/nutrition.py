"""Nutrition analytics from daily summary events."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


class NutritionLike(Protocol):
    """Protocol for nutrition event rows."""

    logged_for: datetime
    calories: int
    protein_g: float
    carbs_g: float
    fat_g: float


@dataclass(frozen=True, slots=True)
class NutritionAverages:
    """Average daily nutrition totals."""

    days_logged: int
    calories: float
    protein_g: float
    carbs_g: float
    fat_g: float


def average_nutrition(
    events: Iterable[NutritionLike],
    start: datetime,
    end: datetime,
) -> NutritionAverages:
    """Compute average nutrition values across the full period.

    A calendar day in [start, end] without a nutrition log is treated as an
    unlogged day substantially below goal, not excluded from the average.
    """

    if end < start:
        raise ValueError("end must be on or after start")
    event_list = [
        event for event in events if start.date() <= event.logged_for.date() <= end.date()
    ]
    total_days = (end.date() - start.date()).days + 1
    if total_days == 0:
        return NutritionAverages(len(event_list), 0.0, 0.0, 0.0, 0.0)

    return NutritionAverages(
        days_logged=len(event_list),
        calories=sum(event.calories for event in event_list) / total_days,
        protein_g=sum(event.protein_g for event in event_list) / total_days,
        carbs_g=sum(event.carbs_g for event in event_list) / total_days,
        fat_g=sum(event.fat_g for event in event_list) / total_days,
    )


def _goal_adherence_rate(
    events: Iterable[NutritionLike],
    field: str,
    goal: float,
    start: datetime,
    end: datetime,
) -> float:
    """Share of days in the period whose logged value for `field` met `goal`.

    Days without a nutrition log count against adherence rather than being
    excluded, since an unlogged day is assumed to be well below goal.
    """

    if end < start:
        raise ValueError("end must be on or after start")
    total_days = (end.date() - start.date()).days + 1
    if total_days == 0:
        return 0.0
    met_days = {
        event.logged_for.date()
        for event in events
        if start.date() <= event.logged_for.date() <= end.date() and getattr(event, field) >= goal
    }
    return len(met_days) / total_days


def protein_goal_adherence(
    events: Iterable[NutritionLike],
    protein_goal_g: float,
    start: datetime,
    end: datetime,
) -> float:
    """Return the share of days in the period that met the protein goal."""

    return _goal_adherence_rate(events, "protein_g", protein_goal_g, start, end)


def carbs_goal_adherence(
    events: Iterable[NutritionLike],
    carbs_goal_g: float,
    start: datetime,
    end: datetime,
) -> float:
    """Return the share of days in the period that met the carb goal."""

    return _goal_adherence_rate(events, "carbs_g", carbs_goal_g, start, end)


def fat_goal_adherence(
    events: Iterable[NutritionLike],
    fat_goal_g: float,
    start: datetime,
    end: datetime,
) -> float:
    """Return the share of days in the period that met the fat goal."""

    return _goal_adherence_rate(events, "fat_g", fat_goal_g, start, end)


def calorie_goal_adherence(
    events: Iterable[NutritionLike],
    calorie_goal: float,
    start: datetime,
    end: datetime,
) -> float:
    """Return the share of days in the period that met the calorie goal."""

    return _goal_adherence_rate(events, "calories", calorie_goal, start, end)


def missing_nutrition_days(
    events: Iterable[NutritionLike],
    start: datetime,
    end: datetime,
) -> int:
    """Count calendar days in the period without nutrition logs."""

    if end < start:
        raise ValueError("end must be on or after start")
    logged = {event.logged_for.date() for event in events}
    total_days = (end.date() - start.date()).days + 1
    return total_days - len({day for day in logged if start.date() <= day <= end.date()})
