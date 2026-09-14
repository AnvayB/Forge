from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from fitness_coach.coach.tools import TOOL_SCHEMAS, CoachToolkit, _parse_day_exercises, _parse_split
from fitness_coach.config.settings import CoachSettings
from fitness_coach.database import models
from fitness_coach.database.repositories import (
    CardioEventRepository,
    CommitmentEventRepository,
    ConversationMemoryRepository,
    ExerciseBaselineRepository,
    InjuryHistoryRepository,
    MeasurementEventRepository,
    NutritionEventRepository,
    PlanOverrideRepository,
    SleepEventRepository,
    UserRepository,
    WorkoutEventRepository,
)
from fitness_coach.database.session import create_db_engine, create_session_factory, init_db
from fitness_coach.research.engine import NullResearchProvider, ResearchEngine
from fitness_coach.research.knowledge_base import KnowledgeBase
from fitness_coach.routing.classifier import ALL_CHAT_TOOLS

NOW = datetime(2026, 9, 15, 18, 0, tzinfo=UTC)  # a Tuesday


@pytest.fixture
def toolkit(tmp_path: Path):
    engine = create_db_engine(f"sqlite:///{tmp_path / 'tools.db'}")
    init_db(engine)
    session = create_session_factory(engine)()
    user = UserRepository(session).get_or_create_single_user(discord_user_id="1")
    workouts = WorkoutEventRepository(session)
    kit = CoachToolkit(
        settings=CoachSettings(),
        config_dir=Path("config"),
        workouts=workouts,
        cardio=CardioEventRepository(session),
        nutrition=NutritionEventRepository(session),
        sleep=SleepEventRepository(session),
        measurements=MeasurementEventRepository(session),
        commitments=CommitmentEventRepository(session),
        plan_overrides=PlanOverrideRepository(session),
        exercise_baselines=ExerciseBaselineRepository(session),
        memory=ConversationMemoryRepository(session),
        injuries=InjuryHistoryRepository(session),
        research=ResearchEngine(
            KnowledgeBase(Path("config/knowledge_base.md")), NullResearchProvider()
        ),
        now=lambda: NOW,
    )
    kit.bind(user.id, research_allowed=True, risk=False)
    # Bench stalled at 185x5 for four sessions after one improvement.
    for weeks_ago, weight in ((5, 175), (4, 185), (3, 185), (2, 185), (1, 185)):
        workouts.add(
            models.WorkoutEvent(
                user_id=user.id,
                occurred_at=NOW - timedelta(weeks=weeks_ago),
                workout_type="Upper",
                exercises=[
                    {"name": "Flat Bench Press", "sets": [{"weight": weight, "reps": 5}] * 3},
                    {"name": "Lat Pulldown", "sets": [{"weight": 120, "reps": 10}]},
                ],
            )
        )
    session.flush()
    yield kit, session, user
    session.close()


def test_every_chat_tool_has_a_schema_and_handler(toolkit) -> None:
    kit, _, _ = toolkit
    for name in ALL_CHAT_TOOLS:
        assert name in TOOL_SCHEMAS
        assert name in kit._handlers()
    assert kit.schemas_for(("get_recent_events", "nope"))[0]["name"] == "get_recent_events"


def test_exercise_history_reports_raw_and_derived_stall(toolkit) -> None:
    kit, _, _ = toolkit
    payload = json.loads(
        kit.dispatch("get_exercise_history", json.dumps({"exercise_name": "bench"}))
    )
    assert payload["exercises"][0]["exercise"] == "Flat Bench Press"
    derived = payload["exercises"][0]["derived"]
    assert derived["sessions"] == 5
    assert derived["last_top_set"] == {"weight": 185, "reps": 5}
    assert derived["consecutive_non_improving_sessions"] == 3
    assert derived["stalled"] is True
    assert derived["best_estimated_1rm"] == round(185 * (1 + 5 / 30), 1)
    assert payload["exercises"][0]["raw"]["sessions"][0]["sets"][0] == {"weight": 175, "reps": 5}
    assert kit.calls[-1]["name"] == "get_exercise_history"
    assert kit.calls[-1]["ok"] is True


def test_exercise_history_no_match(toolkit) -> None:
    kit, _, _ = toolkit
    payload = json.loads(kit.dispatch("get_exercise_history", {"exercise_name": "leg press"}))
    assert payload["exercises"] == []
    assert "No sessions" in payload["note"]


def test_recent_events_are_bounded_and_raw(toolkit) -> None:
    kit, _, _ = toolkit
    payload = json.loads(kit.dispatch("get_recent_events", {"event_type": "workout", "days": 30}))
    assert payload["window_days"] == 14
    assert len(payload["raw"]["workouts"]) == 2
    assert "trend" not in json.dumps(payload["raw"])


def test_todays_plan_uses_training_preferences(toolkit) -> None:
    kit, _, _ = toolkit
    payload = json.loads(kit.dispatch("get_todays_plan", {}))
    assert payload["weekday"] == "Tuesday"
    assert payload["scheduled_focus"] == "Chest + Back + Shoulders"
    assert payload["exercises"][0] == "Lat pulldown"
    assert payload["is_rest_day"] is False
    assert payload["is_cardio_day"] is False
    friday = json.loads(kit.dispatch("get_todays_plan", {"date": "2026-09-18"}))
    assert friday["is_rest_day"] is True
    assert friday["is_cardio_day"] is True


def test_parse_helpers_on_real_config() -> None:
    text = Path("config/training_preferences.md").read_text(encoding="utf-8")
    split = _parse_split(text)
    assert split["Thursday"] == "Legs"
    assert split["Monday"] == "Rest"
    assert "Bicep curls" in _parse_day_exercises(text, "Wednesday")


def test_constraints_include_avoid_list_and_injuries(toolkit) -> None:
    kit, _, _ = toolkit
    first = kit.record_pain_report("elbow", "hurts when curling")
    assert first["injury"]["reports"] == 1
    second = json.loads(
        kit.dispatch("log_injury", {"body_area": "Elbow", "description": "again", "severity": 2})
    )
    assert second["injury"]["reports"] == 2
    payload = json.loads(kit.dispatch("get_active_constraints", "{}"))
    assert payload["active_injuries"][0]["body_area"] == "elbow"
    assert payload["active_injuries"][0]["severity"] == 2
    assert "Skull crushers (elbow discomfort)" in payload["exercises_to_avoid"]


def test_nutrition_targets_come_from_settings(toolkit) -> None:
    kit, _, _ = toolkit
    payload = json.loads(kit.dispatch("get_nutrition_targets", None))
    assert payload["targets"]["protein_g"] == 160
    assert payload["today_logged"] is None


def test_search_kb_and_external_research_budget(toolkit) -> None:
    kit, _, _ = toolkit
    kb = json.loads(kit.dispatch("search_knowledge_base", {"topic": "deload"}))
    assert any("Deload" in e["topic"] for e in kb["entries"])
    first = json.loads(kit.dispatch("flag_for_external_research", {"topic": "creatine timing"}))
    assert first["category"] == "insufficient_evidence"
    second = json.loads(kit.dispatch("flag_for_external_research", {"topic": "creatine timing"}))
    assert "exhausted" in second["error"]


def test_remember_fact_normalizes_key(toolkit) -> None:
    kit, session, user = toolkit
    payload = json.loads(
        kit.dispatch("remember_fact", {"key": "Preferred Training Time", "value": "evenings"})
    )
    assert payload["remembered"]["key"] == "preferred_training_time"
    facts = ConversationMemoryRepository(session).all_for_user(user.id)
    assert facts[0].value["value"] == "evenings"


def test_unknown_tool_and_bad_args_are_reported_not_raised(toolkit) -> None:
    kit, _, _ = toolkit
    assert "Unknown tool" in kit.dispatch("does_not_exist", "{}")
    assert "Bad arguments" in kit.dispatch("get_exercise_history", {"nope": 1})
