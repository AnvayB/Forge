"""Set classification: warm-up vs working vs back-off.

Sets are stored as plain ``{"weight", "reps"}`` dicts. A set type is only stored when the
user stated it explicitly; otherwise it is inferred here, at read time, so history is never
rewritten and better inference applies retroactively.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Any

# A set before the top set counts as a warm-up only when it is clearly lighter than it.
DEFAULT_WARMUP_MAX_RATIO = 0.8

_LEADING_NUMBER = re.compile(r"^\s*(\d+(?:\.\d+)?)")


class SetType(StrEnum):
    WARMUP = "warmup"
    WORKING = "working"
    BACKOFF = "backoff"


def parse_number(value: Any) -> float | None:
    """Read a number from a value the vision model may have left as text ("30 lbs")."""

    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        match = _LEADING_NUMBER.match(value)
        return float(match.group(1)) if match else None
    return None


def explicit_set_type(item: Mapping[str, Any]) -> SetType | None:
    """The type the user stated for this set, or None when absent or unrecognised."""

    try:
        return SetType(item.get("set_type"))
    except ValueError:
        return None


def _usable(item: Mapping[str, Any]) -> tuple[float, float] | None:
    weight, reps = parse_number(item.get("weight")), parse_number(item.get("reps"))
    if weight and reps and weight > 0 and reps > 0:
        return weight, reps
    return None


def classify_sets(
    sets: Sequence[Mapping[str, Any]], *, warmup_max_ratio: float = DEFAULT_WARMUP_MAX_RATIO
) -> list[SetType]:
    """Return a type per set, in the order given.

    An explicit ``set_type`` always wins. Otherwise: the first set at the heaviest weight is
    the top (working) set; earlier sets at or below ``warmup_max_ratio`` of that weight are
    warm-ups; later sets are back-offs. Sets without a usable weight and rep count cannot be
    judged, so they are neutral ``working`` sets (they contribute no volume anyway).
    """

    usable = [_usable(item) for item in sets]
    weights = [pair[0] for pair in usable if pair]
    top_weight = max(weights, default=0.0)
    top_index = next(
        (i for i, pair in enumerate(usable) if pair and pair[0] == top_weight), len(sets)
    )

    types: list[SetType] = []
    for index, (item, pair) in enumerate(zip(sets, usable, strict=True)):
        explicit = explicit_set_type(item)
        if explicit is not None:
            types.append(explicit)
        elif pair is None or index == top_index:
            types.append(SetType.WORKING)
        elif index > top_index:
            types.append(SetType.BACKOFF)
        elif pair[0] <= top_weight * warmup_max_ratio:
            types.append(SetType.WARMUP)
        else:
            types.append(SetType.WORKING)
    return types


def _normalize_set(item: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(item)
    for key in ("weight", "reps"):
        if isinstance(result.get(key), str):
            parsed = parse_number(result[key])
            if parsed is not None:
                result[key] = int(parsed) if parsed.is_integer() else parsed
    explicit = explicit_set_type(result)
    result.pop("set_type", None)
    if explicit is not None:
        result["set_type"] = explicit.value
    return result


def _is_matching_pair(first: Mapping[str, Any], second: Mapping[str, Any]) -> bool:
    """Arrow lists the right and left hand as two rows; equal rows are one two-sided set."""

    return (
        {first.get("side"), second.get("side")} == {"R", "L"}
        and first.get("weight") == second.get("weight")
        and first.get("reps") == second.get("reps")
    )


def _merge_sides(sets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    index = 0
    while index < len(sets):
        current = sets[index]
        following = sets[index + 1] if index + 1 < len(sets) else None
        if following is not None and _is_matching_pair(current, following):
            combined = {key: value for key, value in current.items() if key != "side"}
            if "set_type" not in combined and "set_type" in following:
                combined["set_type"] = following["set_type"]
            combined["sides"] = 2
            merged.append(combined)
            index += 2
        else:
            merged.append(current)
            index += 1
    return merged


def normalize_exercises(exercises: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Clean extracted exercises before storage without mutating the input.

    Coerces numeric text ("30 lbs"), drops unrecognised ``set_type`` values and merges
    adjacent matching right/left rows into one set with ``sides: 2``. The legacy flat shape
    (``sets`` is a count) is passed through unchanged.
    """

    result: list[dict[str, Any]] = []
    for exercise in exercises:
        copy = dict(exercise)
        sets = copy.get("sets")
        if isinstance(sets, list):
            copy["sets"] = _merge_sides([_normalize_set(item) for item in sets])
        result.append(copy)
    return result


_SET_TYPE_ALIASES = {
    "w": SetType.WARMUP,
    "warmup": SetType.WARMUP,
    "warm-up": SetType.WARMUP,
    "working": SetType.WORKING,
    "work": SetType.WORKING,
    "top": SetType.WORKING,
    "bo": SetType.BACKOFF,
    "backoff": SetType.BACKOFF,
    "back-off": SetType.BACKOFF,
}


def parse_set_type(text: str) -> SetType | None:
    """Map a typed word ("w", "warm-up", "bo", "top") to a set type."""

    return _SET_TYPE_ALIASES.get(text.strip().lower())


def parse_set_numbers(spec: str) -> list[int] | None:
    """Parse "2", "1-3" or "1,3" into 1-based set numbers; None when malformed."""

    numbers: list[int] = []
    for part in spec.replace(" ", "").split(","):
        low, dash, high = part.partition("-")
        if not low.isdigit() or (dash and not high.isdigit()):
            return None
        start, end = int(low), int(high) if dash else int(low)
        if start < 1 or end < start:
            return None
        numbers.extend(range(start, end + 1))
    return numbers or None
