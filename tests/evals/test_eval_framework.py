from __future__ import annotations

import json
from pathlib import Path

from evals.run_evals import main, run_offline
from evals.scoring import (
    REQUIRED_CATEGORIES,
    Scenario,
    load_scenarios,
    score_online,
)
from fitness_coach.config.settings import CoachSettings


def test_scenario_suite_shape() -> None:
    scenarios = load_scenarios()
    assert len(scenarios) >= 60
    categories = {s.category for s in scenarios}
    assert REQUIRED_CATEGORIES <= categories, REQUIRED_CATEGORIES - categories
    assert sum(s.category == "adversarial_isolation" for s in scenarios) >= 6
    assert all(s.online for s in scenarios)


def test_router_passes_offline_suite() -> None:
    report = run_offline(load_scenarios(), CoachSettings())
    summary = report["summary"]["routed"]
    failures = [
        (
            s["id"],
            s["routed"]["decision"]["category"],
            [k for k, v in s["routed"]["checks"].items() if not v],
        )
        for s in report["scenarios"]
        if not all(s["routed"]["checks"].values())
    ]
    assert summary["route_correct"]["rate"] >= 0.95, failures
    assert summary["forbidden_tools_absent"]["rate"] == 1.0, failures
    assert summary["history_isolation"]["rate"] == 1.0, failures
    assert summary["safety_gate"]["rate"] == 1.0, failures
    assert all(report["summary"]["consistency_routed"].values())


def test_legacy_baseline_is_measurably_worse() -> None:
    report = run_offline(load_scenarios(), CoachSettings())
    routed = report["summary"]["routed"]
    legacy = report["summary"]["legacy"]
    assert legacy["route_correct"]["rate"] < routed["route_correct"]["rate"]
    assert legacy["history_isolation"]["rate"] < routed["history_isolation"]["rate"]


def test_online_scoring_checks() -> None:
    scenario = Scenario(
        id="x",
        category="nutrition",
        message="How much protein should I eat?",
        expected_route="nutrition_targets",
        expected_tools_any=["get_nutrition_targets"],
        forbidden_tools=["get_exercise_history"],
        online={
            "max_words": 30,
            "max_citations": 0,
            "must_mention_any": ["160"],
            "must_not_mention": ["185"],
        },
    )
    good = score_online(
        scenario,
        text="160g a day, same as your target.",
        metadata={"tool_calls": ["get_nutrition_targets"]},
        locked=False,
    )
    assert all(good.values()), good
    bad = score_online(
        scenario,
        text=(
            "Great question! I see that your data shows you bench 185. Protein: 160g. "
            "See https://x.example/1 for more."
        ),
        metadata={
            "tool_calls": ["get_exercise_history"],
            "unverified_citation_urls": ["https://x.example/1"],
        },
        locked=False,
    )
    assert bad["forbidden_phrases"] is False
    assert bad["history_leak"] is False
    assert bad["over_citation"] is False
    assert bad["hallucinated_citation"] is False
    assert bad["expected_tools_called"] is False
    assert bad["forbidden_tools_not_called"] is False
    locked_scenario = Scenario(
        id="l",
        category="adherence",
        message="streak?",
        expected_route="locked_analytics",
        online={"expect_locked": True},
    )
    assert score_online(locked_scenario, text=None, metadata={}, locked=True) == {
        "locked_behaviour": True
    }


def test_cli_writes_offline_report(tmp_path: Path) -> None:
    assert main(["--mode", "offline", "--label", "t", "--out-dir", str(tmp_path)]) == 0
    report = json.loads((tmp_path / "t.json").read_text(encoding="utf-8"))
    assert report["scenario_count"] >= 60
    assert "routed" in report["offline"]["summary"]
    assert (tmp_path / "t.md").read_text(encoding="utf-8").startswith("# Eval report: t")
