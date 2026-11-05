from __future__ import annotations

import pytest

from fitness_coach.analytics.readiness import sleep_readiness


@pytest.mark.parametrize(
    ("minutes", "tier"),
    [
        (450, "normal"),
        (420, "normal"),
        (419, "usable"),
        (385, "usable"),
        (360, "usable"),
        (359, "low"),
        (300, "low"),
    ],
)
def test_sleep_tiers_follow_the_configured_thresholds(minutes: int, tier: str) -> None:
    readiness = sleep_readiness(minutes)

    assert readiness is not None and readiness.tier == tier


def test_no_sleep_data_means_no_adjustment_rather_than_a_guess() -> None:
    assert sleep_readiness(None) is None
    assert sleep_readiness(0) is None


def test_thresholds_are_configurable() -> None:
    assert sleep_readiness(400, normal_minutes=390, low_minutes=330).tier == "normal"


def test_usable_sleep_keeps_the_plan_but_rules_out_pr_attempts() -> None:
    guidance = sleep_readiness(385).guidance.lower()

    assert "2+ reps in reserve" in guidance
    assert "pr" in guidance


def test_low_sleep_trims_volume_and_never_asks_for_a_grind() -> None:
    readiness = sleep_readiness(300)

    assert "one set" in readiness.guidance.lower()
    assert "failure" not in readiness.guidance.lower()
    assert readiness.label == "5h00"


def test_label_formats_hours_and_minutes() -> None:
    assert sleep_readiness(385).label == "6h25"
