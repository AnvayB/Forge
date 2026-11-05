"""Sleep analytics for the periodic review."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any, Protocol

SHORT_NIGHT_MINUTES = 360


class SleepLike(Protocol):
    """Protocol for sleep event rows."""

    logged_for: datetime
    time_asleep_minutes: int | None


def summarize_sleep(events: Iterable[SleepLike], start: datetime, end: datetime) -> dict[str, Any]:
    """Nights with a recorded duration, how often they were logged, and short nights.

    Nights without a duration are ignored rather than counted as zero-hour sleep.
    """

    minutes = [
        event.time_asleep_minutes
        for event in events
        if event.time_asleep_minutes is not None
        and start.date() <= event.logged_for.date() <= end.date()
    ]
    total_days = (end.date() - start.date()).days + 1
    return {
        "nights_logged": len(minutes),
        "logging_rate": len(minutes) / total_days if total_days > 0 else 0.0,
        "average_minutes_asleep": sum(minutes) / len(minutes) if minutes else None,
        "nights_under_6h": sum(1 for value in minutes if value < SHORT_NIGHT_MINUTES),
    }
