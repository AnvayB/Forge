"""Strength analytics from structured workout exercise data."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any, Protocol


class WorkoutLike(Protocol):
    """Protocol for workout event rows."""

    occurred_at: datetime
    workout_type: str
    exercises: list[dict[str, Any]]


def exercise_volume(exercise: dict[str, Any]) -> float:
    """Return estimated volume as sets * reps * weight when all values exist."""

    sets = float(exercise.get("sets") or 0)
    reps = float(exercise.get("reps") or exercise.get("target_reps") or 0)
    weight = float(exercise.get("weight") or exercise.get("weight_lb") or 0)
    return sets * reps * weight


def total_workout_volume(workout: WorkoutLike) -> float:
    """Return total estimated volume for a workout."""

    return sum(exercise_volume(exercise) for exercise in workout.exercises)


def volume_by_exercise(workouts: Iterable[WorkoutLike]) -> dict[str, float]:
    """Aggregate estimated volume by exercise name."""

    totals: dict[str, float] = {}
    for workout in workouts:
        for exercise in workout.exercises:
            name = str(exercise.get("name", "unknown")).lower()
            totals[name] = totals.get(name, 0.0) + exercise_volume(exercise)
    return totals


def best_weight_by_exercise(workouts: Iterable[WorkoutLike]) -> dict[str, float]:
    """Return best recorded weight by exercise."""

    best: dict[str, float] = {}
    for workout in workouts:
        for exercise in workout.exercises:
            name = str(exercise.get("name", "unknown")).lower()
            weight = float(exercise.get("weight") or exercise.get("weight_lb") or 0)
            best[name] = max(best.get(name, 0.0), weight)
    return best


def sessions_by_type(workouts: Iterable[WorkoutLike]) -> dict[str, int]:
    """Count workout sessions by workout type."""

    counts: dict[str, int] = {}
    for workout in workouts:
        counts[workout.workout_type] = counts.get(workout.workout_type, 0) + 1
    return counts
