from __future__ import annotations

from fitness_coach.analytics.sets import SetType, classify_sets, normalize_exercises
from fitness_coach.analytics.strength import exercise_volume, working_volume


def _sets(*pairs: tuple[float, int]) -> list[dict[str, float]]:
    return [{"weight": weight, "reps": reps} for weight, reps in pairs]


def test_straight_sets_at_one_weight_have_no_warmups() -> None:
    types = classify_sets(_sets((185, 5), (185, 5), (185, 5)))

    assert SetType.WARMUP not in types
    assert types[0] == SetType.WORKING


def test_reverse_pyramid_has_top_set_first_then_back_offs() -> None:
    assert classify_sets(_sets((100, 6), (90, 8), (90, 9))) == [
        SetType.WORKING,
        SetType.BACKOFF,
        SetType.BACKOFF,
    ]


def test_warmup_boundary_is_80_percent_of_the_top_set() -> None:
    at_boundary = classify_sets(_sets((80, 8), (100, 5)))
    just_above = classify_sets(_sets((81, 8), (100, 5)))

    assert at_boundary[0] == SetType.WARMUP
    assert just_above[0] == SetType.WORKING


def test_single_set_and_empty_list_are_handled() -> None:
    assert classify_sets(_sets((50, 10))) == [SetType.WORKING]
    assert classify_sets([]) == []


def test_explicit_set_type_always_wins_over_inference() -> None:
    sets = [
        {"weight": 20, "reps": 10, "set_type": "working"},
        {"weight": 40, "reps": 5},
        {"weight": 40, "reps": 8, "set_type": "warmup"},
    ]

    assert classify_sets(sets) == [SetType.WORKING, SetType.WORKING, SetType.WARMUP]


def test_unknown_set_type_value_is_ignored_and_inferred_instead() -> None:
    sets = [{"weight": 20, "reps": 10, "set_type": "dropset"}, {"weight": 40, "reps": 5}]

    assert classify_sets(sets) == [SetType.WARMUP, SetType.WORKING]


def test_incomplete_and_dirty_sets_never_crash_and_do_not_distort_the_top_set() -> None:
    sets = [
        {"weight": "20 lbs", "reps": "10"},
        {"weight": None, "reps": 8},
        {"weight": 40, "reps": 0},
        {"weight": 40, "reps": 5},
    ]

    types = classify_sets(sets)

    assert len(types) == 4
    assert types[0] == SetType.WARMUP
    assert types[3] == SetType.WORKING


def test_ramp_then_top_set_then_back_offs_is_classified_without_any_tags() -> None:
    sets = _sets((20, 10), (25, 8), (30, 6), (40, 5), (35, 8), (35, 8))

    assert classify_sets(sets) == [
        SetType.WARMUP,
        SetType.WARMUP,
        SetType.WARMUP,
        SetType.WORKING,
        SetType.BACKOFF,
        SetType.BACKOFF,
    ]


def _row(weight: float, reps: int, side: str) -> dict[str, object]:
    return {"weight": weight, "reps": reps, "side": side}


def test_matching_right_and_left_rows_merge_into_one_two_sided_set() -> None:
    exercises = [
        {
            "name": "Dumbbell Shoulder Press",
            "sets": [_row(25, 10, "R"), _row(25, 10, "L"), _row(27.5, 8, "R"), _row(27.5, 8, "L")],
        }
    ]

    normalized = normalize_exercises(exercises)

    assert normalized[0]["sets"] == [
        {"weight": 25, "reps": 10, "sides": 2},
        {"weight": 27.5, "reps": 8, "sides": 2},
    ]


def test_unequal_right_and_left_rows_stay_separate() -> None:
    exercises = [{"name": "Dumbbell Curl", "sets": [_row(20, 10, "R"), _row(20, 8, "L")]}]

    normalized = normalize_exercises(exercises)

    assert [item["reps"] for item in normalized[0]["sets"]] == [10, 8]
    assert all("sides" not in item for item in normalized[0]["sets"])


def test_identical_rows_without_side_labels_are_never_merged() -> None:
    exercises = [{"name": "Bench Press", "sets": [{"weight": 185, "reps": 5}] * 3}]

    assert len(normalize_exercises(exercises)[0]["sets"]) == 3


def test_normalize_coerces_numeric_text_and_drops_invalid_set_types() -> None:
    exercises = [
        {
            "name": "Cable Row",
            "sets": [
                {"weight": "30 lbs", "reps": "12", "set_type": "warmup"},
                {"weight": 60, "reps": 8, "set_type": "dropset"},
            ],
        }
    ]

    sets = normalize_exercises(exercises)[0]["sets"]

    assert sets[0] == {"weight": 30, "reps": 12, "set_type": "warmup"}
    assert sets[1] == {"weight": 60, "reps": 8}


def test_normalize_leaves_legacy_flat_shape_and_input_untouched() -> None:
    legacy = [{"name": "Squat", "sets": 3, "reps": 5, "weight": 225}]
    original = [{"name": "Row", "sets": [_row(40, 10, "R"), _row(40, 10, "L")]}]

    assert normalize_exercises(legacy) == legacy
    normalize_exercises(original)
    assert original[0]["sets"][0]["side"] == "R"


def test_merging_right_and_left_rows_does_not_change_volume() -> None:
    rows = {
        "name": "Dumbbell Press",
        "sets": [_row(25, 10, "R"), _row(25, 10, "L"), _row(30, 8, "R"), _row(30, 8, "L")],
    }

    merged = normalize_exercises([rows])[0]

    assert exercise_volume(merged) == exercise_volume(rows) == 25 * 10 * 2 + 30 * 8 * 2


def test_working_volume_excludes_inferred_warmups_but_keeps_back_offs() -> None:
    exercise = {"name": "Press", "sets": _sets((20, 10), (25, 8), (40, 5), (35, 8))}

    assert exercise_volume(exercise) == 200 + 200 + 200 + 280
    assert working_volume(exercise) == 200 + 280


def test_working_volume_respects_an_explicit_reclassification() -> None:
    exercise = {
        "name": "Press",
        "sets": [
            {"weight": 40, "reps": 5, "set_type": "warmup"},
            {"weight": 40, "reps": 8},
        ],
    }

    assert working_volume(exercise) == 40 * 8


def test_working_volume_matches_total_volume_for_the_legacy_flat_shape() -> None:
    legacy = {"name": "Squat", "sets": 3, "reps": 5, "weight": 225}

    assert working_volume(legacy) == exercise_volume(legacy) == 3 * 5 * 225
