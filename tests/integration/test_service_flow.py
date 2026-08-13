from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from PIL import Image

from fitness_coach.coach.factory import ServiceFactory
from fitness_coach.coach.service import AnalyticsLockedError
from fitness_coach.config.prompt_builder import PromptBuilder
from fitness_coach.config.settings import AppSettings, CoachSettings
from fitness_coach.database.repositories import (
    NutritionEventRepository,
    SleepEventRepository,
    WorkoutEventRepository,
)
from fitness_coach.database.schemas import NutritionLog, SleepLog, WorkoutLog
from fitness_coach.vision.processor import ImageKind, VisionExtraction


@pytest.fixture
def factory(tmp_path: Path) -> ServiceFactory:
    return ServiceFactory(
        AppSettings(
            database_url=f"sqlite:///{tmp_path / 'coach.db'}",
            config_dir=Path("config"),
            uploads_dir=tmp_path / "uploads",
        ),
        CoachSettings(preferred_model="test-model"),
    )


def test_service_logs_structured_events(factory: ServiceFactory) -> None:
    with factory.session() as session:
        coach = factory.coach_service(session)
        user = coach.get_user("123")
        workout_response = coach.log_workout(
            user.id,
            WorkoutLog(
                occurred_at=datetime(2026, 7, 1, tzinfo=UTC),
                workout_type="Upper",
                duration_minutes=75,
            ),
        )
        nutrition_response = coach.log_nutrition(
            user.id,
            NutritionLog(
                logged_for=datetime(2026, 7, 1, tzinfo=UTC),
                calories=2100,
                protein_g=160,
                carbs_g=230,
                fat_g=70,
            ),
        )
        workouts = WorkoutEventRepository(session).recent(user.id)
        nutrition_logs = NutritionEventRepository(session).recent(user.id)

    assert workout_response.metadata["event_type"] == "workout_completed"
    assert nutrition_response.metadata["event_type"] == "nutrition_logged"
    assert len(workouts) == 1
    assert workouts[0].workout_type == "Upper"
    assert len(nutrition_logs) == 1
    assert nutrition_logs[0].calories == 2100


def test_log_workout_congratulates_new_personal_record(factory: ServiceFactory) -> None:
    with factory.session() as session:
        coach = factory.coach_service(session)
        user = coach.get_user("123")
        first = coach.log_workout(
            user.id,
            WorkoutLog(
                occurred_at=datetime(2026, 7, 1, tzinfo=UTC),
                workout_type="Arms",
                exercises=[
                    {"name": "Cable Lateral Raise", "sets": [{"weight": 12, "reps": 20}]}
                ],
            ),
        )
        second = coach.log_workout(
            user.id,
            WorkoutLog(
                occurred_at=datetime(2026, 7, 8, tzinfo=UTC),
                workout_type="Arms",
                exercises=[
                    {"name": "Cable Lateral Raise", "sets": [{"weight": 15, "reps": 20}]}
                ],
            ),
        )

    assert first.metadata["personal_records"] == []
    assert "PR" not in first.message
    assert second.metadata["personal_records"] == ["Cable Lateral Raise"]
    assert "New PR" in second.message
    assert "15lbs x20" in second.message
    assert "up from 12lbs x20" in second.message


def test_service_logs_sleep(factory: ServiceFactory) -> None:
    with factory.session() as session:
        coach = factory.coach_service(session)
        user = coach.get_user("123")
        response = coach.log_sleep(
            user.id,
            SleepLog(
                logged_for=datetime(2026, 7, 1, tzinfo=UTC),
                time_asleep_minutes=341,
                regularity_percent=90,
                wake_up_mood="OK",
            ),
        )
        logs = SleepEventRepository(session).recent(user.id)

    assert response.metadata["event_type"] == "sleep_logged"
    assert len(logs) == 1
    assert logs[0].time_asleep_minutes == 341
    assert logs[0].wake_up_mood == "OK"


def test_sleep_screenshot_extraction_stores_structured_summary(factory: ServiceFactory) -> None:
    extraction = VisionExtraction(
        kind=ImageKind.SLEEP_SCREENSHOT,
        confidence=0.9,
        needs_clarification=False,
        facts={
            "time_asleep_minutes": 341,
            "regularity_percent": 90,
            "sleep_latency_minutes": 47,
            "wake_up_mood": "OK",
        },
        retained_path=None,
    )
    with factory.session() as session:
        coach = factory.coach_service(session)
        user = coach.get_user("123")
        response = coach.store_vision_extraction(user.id, extraction)
        logs = SleepEventRepository(session).recent(user.id)

    assert response.metadata["event_type"] == "sleep_logged"
    assert len(logs) == 1
    assert logs[0].sleep_latency_minutes == 47


def test_sleep_screenshot_extraction_without_core_field_asks_for_clarification(
    factory: ServiceFactory,
) -> None:
    extraction = VisionExtraction(
        kind=ImageKind.SLEEP_SCREENSHOT,
        confidence=0.9,
        needs_clarification=False,
        facts={"wake_up_mood": "OK"},
        retained_path=None,
    )
    with factory.session() as session:
        coach = factory.coach_service(session)
        user = coach.get_user("123")
        response = coach.store_vision_extraction(user.id, extraction)

    assert response.metadata["event_type"] == "vision_clarification_required"


def test_prompt_builder_includes_dynamic_context(factory: ServiceFactory) -> None:
    with factory.session() as session:
        coach = factory.coach_service(session)
        user = coach.get_user("123")
        prompt = coach.prompt_builder.build(user.id)

    assert "# Fitness Accountability Coach" in prompt
    assert "# Dynamic SQLite Context" in prompt


def test_analytics_lock_blocks_cumulative_requests(factory: ServiceFactory) -> None:
    with factory.session() as session:
        coach = factory.coach_service(session)
        user = coach.get_user("123")
        with pytest.raises(AnalyticsLockedError):
            coach.answer_question(user.id, "Show me my monthly analytics trend")


def test_vision_processor_deletes_temp_screenshot(
    factory: ServiceFactory, tmp_path: Path
) -> None:
    image_path = tmp_path / "proof.png"
    Image.new("RGB", (10, 10), color="white").save(image_path)

    with factory.session() as session:
        user = factory.coach_service(session).get_user("123")
        processor = factory.vision_processor(session)
        extraction = processor.process(
            user_id=user.id,
            source_path=image_path,
            kind=ImageKind.WORKOUT_SCREENSHOT,
        )

    tmp_files = list((tmp_path / "uploads" / "tmp").glob("*"))
    assert extraction.needs_clarification is True
    assert tmp_files == []


def test_prompt_builder_errors_for_missing_config(tmp_path: Path) -> None:
    builder = PromptBuilder(tmp_path)
    with pytest.raises(FileNotFoundError):
        builder.build("user")
