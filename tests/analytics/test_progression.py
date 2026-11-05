from __future__ import annotations

import pytest

from fitness_coach.analytics.progression import (
    Action,
    equipment_for,
    rep_target_for,
    suggest_next,
)


def _session(*pairs: tuple[float, int]) -> dict[str, object]:
    return {"name": "x", "sets": [{"weight": w, "reps": r} for w, r in pairs]}


# --- rep targets ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "top", "backoff"),
    [
        ("Incline Dumbbell Chest Press", (5, 10), (8, 12)),
        ("High Row Machine", (5, 10), (8, 12)),
        ("Cable Tricep Pushdown", (8, 12), (10, 15)),
        ("Incline Dumbbell Curl", (8, 12), (10, 15)),
        ("Cable Lateral Raise", (12, 20), (12, 20)),
        ("Seated Leg Curl Machine", (10, 15), (10, 15)),  # leg curl, not an arm curl
        ("Leg Extension", (10, 15), (10, 15)),
        ("Mystery Machine Thing", (6, 12), (8, 15)),  # unfamiliar exercise: broad fallback
    ],
)
def test_default_rep_targets_follow_the_exercise_type(
    name: str, top: tuple[int, int], backoff: tuple[int, int]
) -> None:
    target = rep_target_for(name)

    assert (target.top, target.backoff) == (top, backoff)


def test_a_user_override_beats_the_default() -> None:
    overrides = {"lateral raise": {"top": [15, 25], "backoff": [15, 25]}}

    assert rep_target_for("Cable Lateral Raise", overrides).top == (15, 25)
    assert rep_target_for("Bench Press", overrides).top == (5, 10)


@pytest.mark.parametrize(
    ("name", "equipment"),
    [
        ("Incline Dumbbell Curl", "dumbbell"),
        ("Incline Barbell Bench Press", "barbell"),
        ("Cable Tricep Pushdown", "cable"),
        ("Seated Leg Press Machine", "machine"),
        ("Lat Pulldown Machine", "machine"),
        ("Bodyweight Dip", "other"),
    ],
)
def test_equipment_is_inferred_from_the_exercise_name(name: str, equipment: str) -> None:
    assert equipment_for(name) == equipment


# --- suggestions ----------------------------------------------------------------------
# Newest session first. Compound press range is 5-10 reps.


def test_one_great_session_never_triggers_a_weight_increase() -> None:
    history = [_session((40, 10)), _session((40, 8))]

    suggestion = suggest_next("Dumbbell Chest Press", history)

    assert suggestion.action == Action.HOLD
    assert suggestion.weight == 40


def test_two_sessions_at_the_top_of_the_range_earn_an_increase() -> None:
    history = [_session((40, 10)), _session((40, 10)), _session((40, 8))]

    suggestion = suggest_next("Dumbbell Chest Press", history)

    assert suggestion.action == Action.INCREASE_WEIGHT
    assert suggestion.weight == 42.5  # dumbbells move in small steps
    assert suggestion.reps == 5  # back to the bottom of the range at the heavier weight


@pytest.mark.parametrize(
    ("name", "expected_weight"),
    [("Barbell Bench Press", 45), ("Seated Leg Press Machine", 45), ("Cable Row", 45)],
)
def test_increments_depend_on_the_equipment(name: str, expected_weight: float) -> None:
    history = [_session((40, 10)), _session((40, 10))]

    assert suggest_next(name, history).weight == expected_weight


def test_a_user_defined_increment_is_respected() -> None:
    history = [_session((40, 10)), _session((40, 10))]

    suggestion = suggest_next("Dumbbell Chest Press", history, increments={"dumbbell": 5})

    assert suggestion.weight == 45


def test_below_the_top_of_the_range_the_next_step_is_one_more_rep() -> None:
    history = [_session((40, 7)), _session((40, 6))]

    suggestion = suggest_next("Dumbbell Chest Press", history)

    assert (suggestion.action, suggestion.weight, suggestion.reps) == (Action.ADD_REPS, 40, 8)


def test_weight_moves_back_to_the_range_start_not_reps_after_a_jump() -> None:
    history = [_session((42.5, 5)), _session((40, 10)), _session((40, 10))]

    suggestion = suggest_next("Dumbbell Chest Press", history)

    assert suggestion.action == Action.ADD_REPS
    assert (suggestion.weight, suggestion.reps) == (42.5, 6)


def test_two_sessions_under_the_range_ease_off_rather_than_push() -> None:
    history = [_session((60, 3)), _session((60, 4))]

    suggestion = suggest_next("Dumbbell Chest Press", history)

    assert suggestion.action == Action.EASE_OFF
    assert suggestion.weight == 57.5


def test_unfamiliar_exercise_gets_a_ramp_instruction_with_no_numbers() -> None:
    for history in ([], [_session((40, 8))]):
        suggestion = suggest_next("Landmine Press", history)

        assert suggestion.action == Action.FIND_WORKING_WEIGHT
        assert suggestion.weight is None and suggestion.reps is None
        assert "5-10" in suggestion.message


def test_warmups_never_drive_the_suggestion() -> None:
    # Heavy-looking warm-up reps (30x15) must not be read as a top-of-range working set.
    history = [_session((30, 15), (50, 6)), _session((30, 15), (50, 6))]

    suggestion = suggest_next("Dumbbell Chest Press", history)

    assert (suggestion.action, suggestion.weight, suggestion.reps) == (Action.ADD_REPS, 50, 7)


@pytest.mark.parametrize(
    "history",
    [[], [_session((40, 8))], [_session((40, 10)), _session((40, 10))]],
)
def test_suggestions_never_cite_an_estimated_max_or_urge_failure(history) -> None:
    message = suggest_next("Dumbbell Chest Press", history).message.lower()

    assert "1rm" not in message and "max" not in message and "e1rm" not in message
    assert "failure" not in message or "short of failure" in message or "shy of failure" in message


def test_missing_or_unusable_history_is_treated_as_unfamiliar() -> None:
    history = [{"name": "x", "sets": 3}, {"name": "x", "sets": [{"weight": None, "reps": 0}]}]

    assert suggest_next("Dumbbell Chest Press", history).action == Action.FIND_WORKING_WEIGHT
