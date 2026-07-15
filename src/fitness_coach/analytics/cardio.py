"""Cardio analytics."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


class CardioLike(Protocol):
    """Protocol for cardio event rows."""

    occurred_at: datetime
    duration_minutes: int
    distance_miles: float | None
    modality: str


@dataclass(frozen=True, slots=True)
class CardioSummary:
    """Summary of cardio work in a period."""

    sessions: int
    total_minutes: int
    total_distance_miles: float
    average_minutes: float
    modalities: dict[str, int]


def summarize_cardio(events: Iterable[CardioLike]) -> CardioSummary:
    """Summarize cardio sessions without LLM involvement."""

    event_list = list(events)
    total_minutes = sum(event.duration_minutes for event in event_list)
    total_distance = sum(event.distance_miles or 0 for event in event_list)
    modalities: dict[str, int] = {}
    for event in event_list:
        modalities[event.modality] = modalities.get(event.modality, 0) + 1

    sessions = len(event_list)
    return CardioSummary(
        sessions=sessions,
        total_minutes=total_minutes,
        total_distance_miles=round(total_distance, 2),
        average_minutes=total_minutes / sessions if sessions else 0.0,
        modalities=modalities,
    )


def minutes_against_goal(events: Iterable[CardioLike], goal_minutes: int) -> dict[str, float | int]:
    """Compare total cardio minutes against a period goal."""

    summary = summarize_cardio(events)
    return {
        "total_minutes": summary.total_minutes,
        "goal_minutes": goal_minutes,
        "minutes_remaining": max(0, goal_minutes - summary.total_minutes),
        "completion_ratio": summary.total_minutes / goal_minutes if goal_minutes > 0 else 0.0,
    }
