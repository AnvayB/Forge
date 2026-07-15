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


def average_nutrition(events: Iterable[NutritionLike]) -> NutritionAverages:
    """Compute average nutrition values across logged days."""

    event_list = list(events)
    days = len(event_list)
    if days == 0:
        return NutritionAverages(0, 0.0, 0.0, 0.0, 0.0)

    return NutritionAverages(
        days_logged=days,
        calories=sum(event.calories for event in event_list) / days,
        protein_g=sum(event.protein_g for event in event_list) / days,
        carbs_g=sum(event.carbs_g for event in event_list) / days,
        fat_g=sum(event.fat_g for event in event_list) / days,
    )


def protein_goal_adherence(events: Iterable[NutritionLike], protein_goal_g: float) -> float:
    """Return the share of logged days that met the protein goal."""

    event_list = list(events)
    if not event_list:
        return 0.0
    met = sum(1 for event in event_list if event.protein_g >= protein_goal_g)
    return met / len(event_list)


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
