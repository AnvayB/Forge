"""Review quality checks: deterministic, so no exact-text assertions on model output."""

from __future__ import annotations

import json
from pathlib import Path

from evals.scoring import score_review

DATA = Path("evals/data")
METRICS = json.loads((DATA / "october_review_metrics.json").read_text(encoding="utf-8"))
BEFORE = (DATA / "october_review_before.txt").read_text(encoding="utf-8")
AFTER = (DATA / "october_review_after.txt").read_text(encoding="utf-8")


def test_the_current_review_style_fails_on_boilerplate_and_length() -> None:
    checks = score_review(BEFORE, METRICS)

    assert checks["no_boilerplate"] is False
    assert checks["within_word_limit"] is False


def test_a_tight_grounded_review_of_the_same_data_passes_every_check() -> None:
    checks = score_review(AFTER, METRICS)

    assert all(checks.values()), checks
    assert len(AFTER.split()) < len(BEFORE.split()) / 2


def test_a_number_that_is_not_in_the_metrics_is_caught() -> None:
    invented = AFTER + " Your bench press rose 40 lbs this month."

    assert score_review(invented, METRICS)["grounded_numbers"] is False


def test_derived_figures_like_hours_and_percentages_are_accepted() -> None:
    text = "Slept 7.5 hours on average; coverage was 45% and activity completion 35.5%."

    assert score_review(text, METRICS)["grounded_numbers"] is True


def test_encouraging_failure_or_maximal_lifts_is_flagged() -> None:
    text = AFTER + " Next month, go to failure and max out on every set."

    assert score_review(text, METRICS)["no_failure_push"] is False


def test_the_word_limit_is_configurable() -> None:
    assert score_review(AFTER, METRICS, max_words=20)["within_word_limit"] is False
