from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from fitness_coach.coach.factory import ServiceFactory
from fitness_coach.coach.openai_client import OpenAIResult
from fitness_coach.coach.service import AnalyticsLockedError
from fitness_coach.config.settings import AppSettings, CoachSettings
from fitness_coach.database import models
from fitness_coach.database.repositories import InjuryHistoryRepository
from fitness_coach.database.schemas import WorkoutLog
from fitness_coach.research.engine import EXTERNAL_LABEL


@pytest.fixture
def factory(tmp_path: Path) -> ServiceFactory:
    return ServiceFactory(
        AppSettings(
            database_url=f"sqlite:///{tmp_path / 'coach.db'}",
            config_dir=Path("config"),
            uploads_dir=tmp_path / "uploads",
            data_dir=tmp_path / "data",
        ),
        CoachSettings(preferred_model="test-model"),
    )


class _ToolCallingStub:
    """Stub client that calls the requested tools through the executor, then answers."""

    def __init__(self, text: str, calls: list[tuple[str, dict[str, Any]]]) -> None:
        self.text = text
        self.planned = calls
        self.prompts: list[str] = []
        self.tools_seen: list[list[dict[str, Any]] | None] = []
        self.tool_outputs: list[dict[str, Any]] = []

    def respond(self, *, system_prompt: str, user_message: str, **kwargs: Any) -> OpenAIResult:
        self.prompts.append(system_prompt)
        tools = kwargs.get("tools")
        self.tools_seen.append(tools)
        executor = kwargs.get("tool_executor")
        exposed = {tool["name"] for tool in tools or []}
        for name, args in self.planned:
            if executor is not None and name in exposed:
                self.tool_outputs.append(json.loads(executor(name, json.dumps(args))))
        return OpenAIResult(text=self.text, metadata={"model": "stub"})


def _seed_bench(coach, user_id: str) -> None:
    now = datetime.now(UTC)
    for weeks_ago, weight in ((5, 175), (4, 185), (3, 185), (2, 185), (1, 185)):
        coach.log_workout(
            user_id,
            WorkoutLog(
                occurred_at=now - timedelta(weeks=weeks_ago),
                workout_type="Upper",
                exercises=[{"name": "Flat Bench Press", "sets": [{"weight": weight, "reps": 5}]}],
            ),
        )


def test_plateau_question_routes_to_exercise_history_and_calls_tool(
    factory: ServiceFactory,
) -> None:
    with factory.session() as session:
        coach = factory.coach_service(session)
        user = coach.get_user("123")
        _seed_bench(coach, user.id)
        stub = _ToolCallingStub(
            "You've been at 185x5 for four sessions - that's a stall. Drop to 165 for a week.",
            [("get_exercise_history", {"exercise_name": "bench"})],
        )
        coach.openai = stub
        response = coach.answer_question(user.id, "Why has my bench not progressed in 5 weeks?")

    assert response.metadata["route"]["category"] == "exercise_history"
    assert response.metadata["tool_calls"] == ["get_exercise_history"]
    assert stub.tool_outputs[0]["exercises"][0]["derived"]["stalled"] is True
    assert "# Routing Guidance (route: exercise_history)" in stub.prompts[0]
    assert {t["name"] for t in stub.tools_seen[0]} >= {
        "get_exercise_history",
        "get_active_constraints",
    }
    assert "flag_for_external_research" not in {t["name"] for t in stub.tools_seen[0]}


def test_safety_route_records_injury_and_blocks_research(factory: ServiceFactory) -> None:
    with factory.session() as session:
        coach = factory.coach_service(session)
        user = coach.get_user("123")
        coach.openai = _ToolCallingStub("How long has that been going on?", [])
        response = coach.answer_question(user.id, "My elbow hurts when I curl.")
        injuries = InjuryHistoryRepository(session).active_for_user(user.id)

    assert response.metadata["route"]["category"] == "safety"
    assert response.metadata["injury_record"]["injury"]["body_area"] == "elbow"
    assert len(injuries) == 1
    assert injuries[0].status == models.InjuryStatus.MONITORING
    assert response.metadata["route"]["research_allowed"] is False


def test_locked_analytics_message_mentions_review_status(factory: ServiceFactory) -> None:
    with factory.session() as session:
        coach = factory.coach_service(session)
        user = coach.get_user("123")
        with pytest.raises(AnalyticsLockedError) as excinfo:
            coach.answer_question(user.id, "How's my adherence been this month?")
    assert "locked" in str(excinfo.value)
    assert "!progress" in str(excinfo.value)


def test_nutrition_question_never_exposes_history_tools(factory: ServiceFactory) -> None:
    with factory.session() as session:
        coach = factory.coach_service(session)
        user = coach.get_user("123")
        stub = _ToolCallingStub("160g a day.", [("get_nutrition_targets", {})])
        coach.openai = stub
        response = coach.answer_question(user.id, "How much protein should I eat?")
    exposed = {t["name"] for t in stub.tools_seen[0]}
    assert exposed == {"get_nutrition_targets", "search_knowledge_base"}
    assert stub.tool_outputs[0]["targets"]["protein_g"] == 160
    assert response.metadata["route"]["needs_user_history"] is False


def test_conversation_window_is_included_on_follow_up(factory: ServiceFactory) -> None:
    with factory.session() as session:
        coach = factory.coach_service(session)
        user = coach.get_user("123")
        coach.openai = _ToolCallingStub("How long has that been going on?", [])
        coach.answer_question(user.id, "My elbow hurts when I curl.")
        stub = _ToolCallingStub("Drop curls for a few days.", [])
        coach.openai = stub
        coach.answer_question(user.id, "about a week, dull ache")
    assert "# Recent Conversation" in stub.prompts[0]
    assert "My elbow hurts when I curl." in stub.prompts[0]


def test_external_citations_must_be_labeled(factory: ServiceFactory) -> None:
    with factory.session() as session:
        coach = factory.coach_service(session)
        coach.get_user("123")
        coach.toolkit.external_urls = {"https://pubmed.ncbi.nlm.nih.gov/424242/"}
        labeled = coach.citation_report(
            f"Creatine helps. {EXTERNAL_LABEL} Study — https://pubmed.ncbi.nlm.nih.gov/424242/",
            external_urls=coach.toolkit.external_urls,
        )
        unlabeled = coach.citation_report(
            "Creatine helps. See https://pubmed.ncbi.nlm.nih.gov/424242/",
            external_urls=coach.toolkit.external_urls,
        )
        hallucinated = coach.citation_report(
            "See https://made-up.example/paper", external_urls=set()
        )
        kb_ok = coach.citation_report(
            "See https://pubmed.ncbi.nlm.nih.gov/27433992/", external_urls=set()
        )
    assert labeled == {"external_citations": ["https://pubmed.ncbi.nlm.nih.gov/424242/"]}
    assert unlabeled["mislabeled_external_citations"] == ["https://pubmed.ncbi.nlm.nih.gov/424242/"]
    assert hallucinated["unverified_citation_urls"] == ["https://made-up.example/paper"]
    assert kb_ok == {}


def test_legacy_path_has_no_tools_and_keyword_lock(factory: ServiceFactory) -> None:
    with factory.session() as session:
        coach = factory.coach_service(session)
        user = coach.get_user("123")
        stub = _ToolCallingStub("legacy answer", [])
        coach.openai = stub
        response = coach.answer_question(user.id, "Why is my bench stuck?", use_routing=False)
        with pytest.raises(AnalyticsLockedError):
            coach.answer_question(user.id, "what's my average?", use_routing=False)
    assert response.metadata["route"] == {"category": "legacy"}
    assert stub.tools_seen == [None]
    assert "# Routing Guidance" not in stub.prompts[0]
