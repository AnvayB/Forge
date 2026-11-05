"""Morning workout message: last night's sleep becomes deterministic guidance in the prompt."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from fitness_coach.coach.factory import ServiceFactory
from fitness_coach.coach.openai_client import OpenAIResult
from fitness_coach.config.settings import AppSettings, CoachSettings
from fitness_coach.database import models
from fitness_coach.database.schemas import SleepLog
from fitness_coach.scheduler.jobs import build_workout_text


@pytest.fixture
def factory(tmp_path: Path) -> ServiceFactory:
    result = ServiceFactory(
        AppSettings(
            database_url=f"sqlite:///{tmp_path / 'coach.db'}",
            config_dir=Path("config"),
            uploads_dir=tmp_path / "uploads",
        ),
        CoachSettings(preferred_model="test-model"),
    )
    yield result
    result.engine.dispose()


class _CapturingOpenAI:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def respond(self, *, system_prompt: str, user_message: str, **_: object) -> OpenAIResult:
        self.messages.append(user_message)
        return OpenAIResult(text="ok", metadata={})


def _morning_prompt(
    factory: ServiceFactory, *, sleep_minutes: int | None, hours_ago: int = 1
) -> str:
    with factory.session() as session:
        coach = factory.coach_service(session)
        user = coach.get_user("123")
        if sleep_minutes is not None:
            coach.log_sleep(
                user.id, SleepLog(logged_for=datetime.now(UTC), time_asleep_minutes=sleep_minutes)
            )
            event = session.query(models.SleepEvent).one()
            event.created_at = datetime.now(UTC) - timedelta(hours=hours_ago)
        stub = _CapturingOpenAI()
        coach.openai = stub  # type: ignore[assignment]
        build_workout_text(coach, user.id, "America/Los_Angeles")
    return stub.messages[-1]


def test_usable_sleep_adds_a_tier_and_concrete_adjustment_to_the_morning_prompt(
    factory: ServiceFactory,
) -> None:
    prompt = _morning_prompt(factory, sleep_minutes=385)

    assert "6h25" in prompt and "usable" in prompt.lower()
    assert "2+ reps in reserve" in prompt


def test_short_sleep_asks_for_a_trimmed_session(factory: ServiceFactory) -> None:
    prompt = _morning_prompt(factory, sleep_minutes=300)

    assert "one set" in prompt.lower()


def test_no_recent_sleep_leaves_the_prompt_exactly_as_before(factory: ServiceFactory) -> None:
    prompt = _morning_prompt(factory, sleep_minutes=None)

    assert "sleep" not in prompt.lower()


def test_a_sleep_log_from_yesterday_is_not_applied_to_today(factory: ServiceFactory) -> None:
    prompt = _morning_prompt(factory, sleep_minutes=300, hours_ago=30)

    assert "sleep" not in prompt.lower()
