"""Sleep analytics for the periodic review.

Sleep is logged most consistently of any event type, so it gets the same
first-class treatment as nutrition/measurements in the periodic review rather
than only ever appearing as raw recent events.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

SHORT_NIGHT_MINUTES = 360  # 6 hours


class SleepLike(Protocol):
    """Protocol for sleep event rows."""

    logged_for: datetime
    time_asleep_minutes: int | None
    regularity_percent: float | None
    sleep_latency_minutes: int | None
    wake_up_mood: str | None


@dataclass(frozen=True, slots=True)
class SleepSummary:
    """Summary of nightly sleep logged in a period."""

    nights_logged: int
    logging_rate: float
    average_time_asleep_minutes: float | None
    average_regularity_percent: float | None
    average_sleep_latency_minutes: float | None
    nights_under_six_hours: int
    mood_counts: dict[str, int]


def summarize_sleep(events: Iterable[SleepLike], start: datetime, end: datetime) -> SleepSummary:
    """Summarize nightly sleep without LLM involvement.

    Unlike nutrition (where an unlogged day is assumed well below goal and
    zero-filled into the average), a night with no sleep log is just excluded
    from these averages - `logging_rate` reports the gap separately instead of
    dragging every other number toward zero whenever a night is missed. Likewise
    a night logged without a duration is not counted as a zero-hour night, and an
    empty period reports no average rather than inventing one.
    """

    if end < start:
        raise ValueError("end must be on or after start")
    in_period = [event for event in events if start.date() <= event.logged_for.date() <= end.date()]
    total_nights = (end.date() - start.date()).days + 1

    with_duration = [e for e in in_period if e.time_asleep_minutes is not None]
    asleep = [e.time_asleep_minutes for e in with_duration if e.time_asleep_minutes is not None]
    regularity = [e.regularity_percent for e in in_period if e.regularity_percent is not None]
    latency = [e.sleep_latency_minutes for e in in_period if e.sleep_latency_minutes is not None]

    mood_counts: dict[str, int] = {}
    for event in in_period:
        if event.wake_up_mood:
            mood_counts[event.wake_up_mood] = mood_counts.get(event.wake_up_mood, 0) + 1

    return SleepSummary(
        nights_logged=len(with_duration),
        logging_rate=len(with_duration) / total_nights if total_nights else 0.0,
        average_time_asleep_minutes=round(sum(asleep) / len(asleep), 1) if asleep else None,
        average_regularity_percent=(
            round(sum(regularity) / len(regularity), 1) if regularity else None
        ),
        average_sleep_latency_minutes=(round(sum(latency) / len(latency), 1) if latency else None),
        nights_under_six_hours=sum(1 for minutes in asleep if minutes < SHORT_NIGHT_MINUTES),
        mood_counts=mood_counts,
    )
