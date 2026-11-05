"""Double progression: add reps first, add weight only once the top of the range is earned.

Everything here is a suggestion expressed as "weight x reps with 1-3 reps in reserve".
Estimated one-rep maxes rank sets elsewhere but never appear as targets, nothing here
changes a recorded weight, and warm-up sets never drive a recommendation.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from fitness_coach.analytics.strength import _one_rep_max_estimate, top_working_set

DEFAULT_HITS_TO_PROGRESS = 2
"""Sessions in a row at the top of the rep range before adding weight."""

DEFAULT_INCREMENTS: dict[str, float] = {
    "barbell": 5.0,
    "dumbbell": 2.5,
    "machine": 5.0,
    "cable": 5.0,
    "other": 5.0,
}

_SAFETY = "stop 1-3 reps shy of failure"


class Action(StrEnum):
    ADD_REPS = "add_reps"
    INCREASE_WEIGHT = "increase_weight"
    HOLD = "hold"
    EASE_OFF = "ease_off"
    FIND_WORKING_WEIGHT = "find_working_weight"


@dataclass(frozen=True, slots=True)
class RepTarget:
    """Recommended rep ranges: a flexible guide, not a limit."""

    top: tuple[int, int]
    backoff: tuple[int, int]


# Checked in order: the first keyword match wins, so "leg curl" must precede "curl".
_REP_TARGET_RULES: tuple[tuple[tuple[str, ...], RepTarget], ...] = (
    (("lateral raise", "side raise"), RepTarget((12, 20), (12, 20))),
    (("leg curl", "leg extension", "hamstring curl"), RepTarget((10, 15), (10, 15))),
    (
        ("tricep", "bicep", "curl", "pushdown", "kickback", "skull", "overhead extension"),
        RepTarget((8, 12), (10, 15)),
    ),
    (
        ("press", "row", "pulldown", "pull-up", "pull up", "chin-up", "dip"),
        RepTarget((5, 10), (8, 12)),
    ),
)
_FALLBACK_TARGET = RepTarget((6, 12), (8, 15))

_EQUIPMENT_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("dumbbell", ("dumbbell", "dumbell")),
    ("barbell", ("barbell", "smith", "ez bar", "ez-bar")),
    ("cable", ("cable", "pushdown")),
    ("machine", ("machine", "leg press", "pulldown", "hack squat", "pec deck", "high row")),
)


def _pair(value: Any) -> tuple[int, int] | None:
    try:
        low, high = value
        return int(low), int(high)
    except (TypeError, ValueError):
        return None


def rep_target_for(
    name: str, overrides: Mapping[str, Mapping[str, Sequence[int]]] | None = None
) -> RepTarget:
    """The rep ranges for an exercise; a user override (matched by name fragment) wins."""

    lowered = name.lower()
    for fragment, override in (overrides or {}).items():
        if fragment.lower() in lowered:
            top = _pair(override.get("top"))
            if top is not None:
                return RepTarget(top, _pair(override.get("backoff")) or top)
    for keywords, target in _REP_TARGET_RULES:
        if any(keyword in lowered for keyword in keywords):
            return target
    return _FALLBACK_TARGET


def equipment_for(name: str) -> str:
    lowered = name.lower()
    for equipment, keywords in _EQUIPMENT_RULES:
        if any(keyword in lowered for keyword in keywords):
            return equipment
    return "other"


@dataclass(frozen=True, slots=True)
class Suggestion:
    action: Action
    message: str
    weight: float | None = None
    reps: int | None = None


def _fmt(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


def suggest_next(
    name: str,
    sessions: Sequence[Mapping[str, Any]],
    *,
    rep_targets: Mapping[str, Mapping[str, Sequence[int]]] | None = None,
    increments: Mapping[str, float] | None = None,
    hits_to_progress: int = DEFAULT_HITS_TO_PROGRESS,
) -> Suggestion:
    """What to aim for next time, from this exercise's sessions (newest first)."""

    low, high = rep_target_for(name, rep_targets).top
    step = {**DEFAULT_INCREMENTS, **(increments or {})}[equipment_for(name)]
    tops = [top for session in sessions if (top := top_working_set(dict(session))) is not None]

    if len(tops) < 2:
        return Suggestion(
            Action.FIND_WORKING_WEIGHT,
            f"New to you: ramp up until a set feels like 1-3 reps in reserve inside "
            f"{low}-{high} reps, then log it as your working weight; {_SAFETY}.",
        )

    weight, reps = tops[0]
    if reps >= high:
        streak = 0
        for session_weight, session_reps in tops:
            if session_weight != weight or session_reps < high:
                break
            streak += 1
        if streak >= hits_to_progress:
            heavier = weight + step
            return Suggestion(
                Action.INCREASE_WEIGHT,
                f"Try {_fmt(heavier)}lbs for {low}+ reps (you've hit {high} at "
                f"{_fmt(weight)}lbs {streak} sessions running); {_SAFETY}.",
                heavier,
                low,
            )
        return Suggestion(
            Action.HOLD,
            f"Repeat {_fmt(weight)}lbs and aim for {high} reps again; {_SAFETY}.",
            weight,
            int(high),
        )

    if reps < low and tops[1][1] < low:
        lighter = max(weight - step, 0.0)
        return Suggestion(
            Action.EASE_OFF,
            f"Reps have been under {low} two sessions running - try {_fmt(lighter)}lbs and "
            f"rebuild reps; {_SAFETY}.",
            lighter,
            int(low),
        )

    return Suggestion(
        Action.ADD_REPS,
        f"Aim for {_fmt(weight)}lbs x{_fmt(reps + 1)}; {_SAFETY}.",
        weight,
        int(reps + 1),
    )


TREND_THRESHOLD = 0.03
"""A top set must move at least 3% (by estimated max) over the period to count as progress."""

STALL_SESSIONS = 3
"""Sessions in a row without beating the period's best top set."""


@dataclass(frozen=True, slots=True)
class ExerciseTrend:
    exercise: str
    sessions: int
    first: tuple[float, float]
    last: tuple[float, float]
    change: float
    non_improving_sessions: int

    @property
    def stalled(self) -> bool:
        return self.non_improving_sessions >= STALL_SESSIONS


def top_set_trends(workouts: Any) -> list[ExerciseTrend]:
    """Per-exercise top working set across a period (warm-ups never count).

    Only exercises with at least two sessions that have a usable top set produce a trend.
    """

    sessions: dict[str, list[tuple[Any, tuple[float, float]]]] = {}
    display: dict[str, str] = {}
    for workout in sorted(workouts, key=lambda w: w.occurred_at):
        for exercise in workout.exercises:
            name = str(exercise.get("name", "")).strip()
            top = top_working_set(exercise)
            if not name or top is None:
                continue
            key = name.casefold()
            display.setdefault(key, name)
            sessions.setdefault(key, []).append((workout.occurred_at, top))

    trends: list[ExerciseTrend] = []
    for key, entries in sessions.items():
        if len(entries) < 2:
            continue
        scores = [_one_rep_max_estimate(*top) for _, top in entries]
        best = 0.0
        non_improving = 0
        for score in scores:
            if score > best:
                best, non_improving = score, 0
            else:
                non_improving += 1
        trends.append(
            ExerciseTrend(
                exercise=display[key],
                sessions=len(entries),
                first=entries[0][1],
                last=entries[-1][1],
                change=scores[-1] / scores[0] - 1,
                non_improving_sessions=non_improving,
            )
        )
    return trends


def review_highlights(
    trends: Sequence[ExerciseTrend], *, max_progress: int = 3, max_stalled: int = 2
) -> dict[str, list[dict[str, Any]]]:
    """The few exercises worth naming in a review: real progress and real stalls."""

    progress = sorted(
        (t for t in trends if t.change >= TREND_THRESHOLD), key=lambda t: t.change, reverse=True
    )
    stalled = sorted((t for t in trends if t.stalled), key=lambda t: t.sessions, reverse=True)
    return {
        "progress": [
            {
                "exercise": t.exercise,
                "from": {"weight": t.first[0], "reps": t.first[1]},
                "to": {"weight": t.last[0], "reps": t.last[1]},
            }
            for t in progress[:max_progress]
        ],
        "stalled": [
            {"exercise": t.exercise, "sessions": t.sessions} for t in stalled[:max_stalled]
        ],
    }
