"""Body measurement (weight/waist/body-fat) trend analytics."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


class MeasurementLike(Protocol):
    """Protocol for measurement event rows."""

    measured_at: datetime
    body_weight_lb: float | None
    waist_inches: float | None
    body_fat_percent: float | None
    progress_photo_path: str | None


@dataclass(frozen=True, slots=True)
class MeasurementTrend:
    """First-vs-last logged value for one measurement within a period."""

    first_value: float
    first_measured_at: str
    last_value: float
    last_measured_at: str
    change: float


@dataclass(frozen=True, slots=True)
class MeasurementSummary:
    """Summary of body measurements logged in a period."""

    entries_logged: int
    photos_logged: int
    body_weight_lb: MeasurementTrend | None
    waist_inches: MeasurementTrend | None
    body_fat_percent: MeasurementTrend | None


def _trend(events: list[MeasurementLike], field: str) -> MeasurementTrend | None:
    dated = [
        (event.measured_at, value)
        for event in events
        if (value := getattr(event, field)) is not None
    ]
    if len(dated) < 2:
        return None
    dated.sort(key=lambda pair: pair[0])
    first_at, first_value = dated[0]
    last_at, last_value = dated[-1]
    return MeasurementTrend(
        first_value=first_value,
        first_measured_at=first_at.isoformat(),
        last_value=last_value,
        last_measured_at=last_at.isoformat(),
        change=round(last_value - first_value, 2),
    )


def summarize_measurements(events: Iterable[MeasurementLike]) -> MeasurementSummary:
    """Summarize weight/waist/body-fat trends without LLM involvement.

    Uses the first and last logged value per field within the period, since these
    are typically extracted opportunistically from a visible scale/tape reading in a
    progress photo rather than logged every session - most periods won't have a value
    for every field, and some may have too few entries for a trend at all.
    """

    event_list = list(events)
    return MeasurementSummary(
        entries_logged=len(event_list),
        photos_logged=sum(1 for event in event_list if event.progress_photo_path),
        body_weight_lb=_trend(event_list, "body_weight_lb"),
        waist_inches=_trend(event_list, "waist_inches"),
        body_fat_percent=_trend(event_list, "body_fat_percent"),
    )
