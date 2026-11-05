from __future__ import annotations

from pathlib import Path

from fitness_coach.analytics.charts import build_progress_charts


def _names(metrics: dict, tmp_path: Path) -> set[str]:
    return {path.name for path in build_progress_charts(metrics, tmp_path)}


def test_macro_goal_chart_is_drawn_only_when_macro_days_exist(tmp_path: Path) -> None:
    macros = {"calories": 3, "protein": 2, "carbs": 2, "fat": 6}

    with_macros = _names({"nutrition": {"macro_goal_days": macros}}, tmp_path / "a")
    without = _names({"nutrition": {"days_logged": 4}}, tmp_path / "b")

    assert "macro_goal_days.png" in with_macros
    assert "macro_goal_days.png" not in without


def test_adherence_chart_reads_the_renamed_protein_rates(tmp_path: Path) -> None:
    metrics = {"nutrition": {"protein_goal_met_rate": 0.2, "protein_near_goal_rate": 0.3}}

    assert "adherence.png" in _names(metrics, tmp_path)


def test_volume_chart_uses_the_working_volume_metric(tmp_path: Path) -> None:
    metrics = {"workouts": {"volume_by_exercise": {"bench press": 12000}}}

    assert "volume_by_exercise.png" in _names(metrics, tmp_path)
