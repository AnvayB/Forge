"""Strength analytics from structured workout exercise data."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
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


@dataclass(frozen=True)
class PersonalRecord:
    """A newly logged set that beats every prior set on record for that exercise."""

    exercise: str
    weight: float
    reps: float
    previous_weight: float
    previous_reps: float


def _one_rep_max_estimate(weight: float, reps: float) -> float:
    """Estimate one-rep max via the Epley formula, used to rank sets uniformly."""

    if reps <= 1:
        return weight
    return weight * (1 + reps / 30)


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def best_set_by_exercise(workouts: Iterable[WorkoutLike]) -> dict[str, tuple[float, float]]:
    """Return the best (weight, reps) set per exercise, ranked by estimated one-rep max."""

    best: dict[str, tuple[float, float]] = {}
    best_1rm: dict[str, float] = {}
    for workout in workouts:
        for exercise in workout.exercises:
            name = str(exercise.get("name", "unknown")).strip().lower()
            for exercise_set in exercise.get("sets") or []:
                weight = _to_float(exercise_set.get("weight"))
                reps = _to_float(exercise_set.get("reps"))
                if weight <= 0 or reps <= 0:
                    continue
                one_rm = _one_rep_max_estimate(weight, reps)
                if one_rm > best_1rm.get(name, 0.0):
                    best_1rm[name] = one_rm
                    best[name] = (weight, reps)
    return best


def find_new_personal_records(
    *, history: Iterable[WorkoutLike], new_exercises: list[dict[str, Any]]
) -> list[PersonalRecord]:
    """Return exercises from a newly logged workout that beat all prior sets on record.

    Sets are ranked by estimated one-rep max (Epley), so both a heavier weight and more
    reps at a previous best weight can register as a PR. Only the single best qualifying
    set per exercise is reported. An exercise with no prior history isn't counted - a
    first-time exercise has nothing to beat yet.
    """

    previous_best = best_set_by_exercise(history)
    records: list[PersonalRecord] = []
    for exercise in new_exercises:
        raw_name = str(exercise.get("name", "unknown")).strip()
        name = raw_name.lower()
        if name not in previous_best:
            continue
        previous_weight, previous_reps = previous_best[name]
        best_candidate: tuple[float, float] | None = None
        best_candidate_1rm = _one_rep_max_estimate(previous_weight, previous_reps)
        for exercise_set in exercise.get("sets") or []:
            weight = _to_float(exercise_set.get("weight"))
            reps = _to_float(exercise_set.get("reps"))
            if weight <= 0 or reps <= 0:
                continue
            one_rm = _one_rep_max_estimate(weight, reps)
            if one_rm > best_candidate_1rm:
                best_candidate_1rm = one_rm
                best_candidate = (weight, reps)
        if best_candidate is not None:
            records.append(
                PersonalRecord(
                    exercise=raw_name,
                    weight=best_candidate[0],
                    reps=best_candidate[1],
                    previous_weight=previous_weight,
                    previous_reps=previous_reps,
                )
            )
    return records
