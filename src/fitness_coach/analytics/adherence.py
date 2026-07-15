"""Adherence analytics."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, timedelta
from typing import Protocol


class OccurredEvent(Protocol):
    """Protocol for events with an occurrence timestamp."""

    occurred_at: datetime


def unique_event_dates(events: Iterable[OccurredEvent]) -> set[date]:
    """Return unique calendar dates from event timestamps."""

    return {event.occurred_at.date() for event in events}


def current_streak(event_dates: Iterable[date], today: date) -> int:
    """Compute the current consecutive-day streak ending today or yesterday."""

    dates = set(event_dates)
    if not dates:
        return 0

    cursor = today if today in dates else today - timedelta(days=1)
    streak = 0
    while cursor in dates:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def completion_rate(completed_dates: Iterable[date], start: date, end: date) -> float:
    """Return completion rate for inclusive date range."""

    if end < start:
        raise ValueError("end must be on or after start")
    total_days = (end - start).days + 1
    completed = {day for day in completed_dates if start <= day <= end}
    return len(completed) / total_days


def missed_days(completed_dates: Iterable[date], start: date, end: date) -> list[date]:
    """Return dates in the range without a completed event."""

    if end < start:
        raise ValueError("end must be on or after start")
    completed = set(completed_dates)
    days = (end - start).days + 1
    return [
        start + timedelta(days=offset)
        for offset in range(days)
        if start + timedelta(days=offset) not in completed
    ]
