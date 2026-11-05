"""Sleep-to-training guidance: a deterministic rule, so the morning message never depends
on the model remembering to look up last night's sleep.
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_NORMAL_MINUTES = 420
DEFAULT_LOW_MINUTES = 360


@dataclass(frozen=True, slots=True)
class Readiness:
    tier: str
    label: str
    guidance: str


_GUIDANCE = {
    "normal": "Sleep supports the planned session; train as scheduled.",
    "usable": (
        "Keep the planned session but leave 2+ reps in reserve on every set and skip PR "
        "attempts today."
    ),
    "low": (
        "Cut one set from each accessory, skip top-set pushes and PR attempts, and keep "
        "cardio easy."
    ),
}


def sleep_readiness(
    minutes: float | None,
    *,
    normal_minutes: int = DEFAULT_NORMAL_MINUTES,
    low_minutes: int = DEFAULT_LOW_MINUTES,
) -> Readiness | None:
    """Tier last night's sleep; None when there is no duration to judge."""

    if not minutes or minutes <= 0:
        return None
    if minutes >= normal_minutes:
        tier = "normal"
    elif minutes >= low_minutes:
        tier = "usable"
    else:
        tier = "low"
    hours, remainder = divmod(int(round(minutes)), 60)
    return Readiness(tier=tier, label=f"{hours}h{remainder:02d}", guidance=_GUIDANCE[tier])
