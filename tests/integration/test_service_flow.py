from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest
from PIL import Image

from fitness_coach.coach.factory import ServiceFactory
from fitness_coach.coach.openai_client import OpenAIResult
from fitness_coach.coach.service import AnalyticsLockedError
from fitness_coach.config.prompt_builder import PromptBuilder
from fitness_coach.config.settings import AppSettings, CoachSettings
from fitness_coach.database import models
from fitness_coach.database.repositories import (
    CardioEventRepository,
    MeasurementEventRepository,
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


def test_workout_baseline_status_is_judged_and_promotes_after_five_sessions(
    factory: ServiceFactory,
) -> None:
    with factory.session() as session:
        coach = factory.coach_service(session)
        user = coach.get_user("123")
        coach.set_exercise_baseline(user.id, "Bench Press", 135, 185)

        def log(day: int, weight: float) -> str:
            response = coach.log_workout(
                user.id,
                WorkoutLog(
                    occurred_at=datetime(2026, 7, day, tzinfo=UTC),
                    workout_type="Push",
                    exercises=[
                        {"name": "Bench Press", "sets": [{"weight": weight, "reps": 8}]}
                    ],
                ),
            )
            return response.message

        assert "Status: Bench Press — Good" in log(1, 135)
        assert "Status: Bench Press — Under" in log(2, 125)
        assert "Status: Bench Press — Over" in log(3, 150)

        # 5 consecutive sessions at 145 promotes the baseline to 145.
        for day in range(4, 8):
            assert "Baseline updated" not in log(day, 145)
        promoted_message = log(8, 145)
        assert "Baseline updated: Bench Press is now 145lbs" in promoted_message

        baselines = coach.list_exercise_baselines(user.id)
    assert len(baselines) == 1
    assert baselines[0].baseline_weight == 145


def test_set_exercise_baseline_overwrite_resets_streak(factory: ServiceFactory) -> None:
    with factory.session() as session:
        coach = factory.coach_service(session)
        user = coach.get_user("123")
        coach.set_exercise_baseline(user.id, "Squat", 135)
        coach.log_workout(
            user.id,
            WorkoutLog(
                occurred_at=datetime(2026, 7, 1, tzinfo=UTC),
                workout_type="Legs",
                exercises=[{"name": "Squat", "sets": [{"weight": 145, "reps": 5}]}],
            ),
        )
        coach.set_exercise_baseline(user.id, "Squat", 145)
        baselines = coach.list_exercise_baselines(user.id)
    assert baselines[0].baseline_weight == 145
    assert baselines[0].tracked_weight is None
    assert baselines[0].consecutive_sessions_at_tracked_weight == 0


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
    assert "# Knowledge Base" in prompt


def test_known_citation_urls_parses_source_lines(tmp_path: Path) -> None:
    for name in PromptBuilder.REQUIRED_FILES:
        (tmp_path / name).write_text("placeholder", encoding="utf-8")
    (tmp_path / "knowledge_base.md").write_text(
        "## Progressive Overload\n\nSource: NSCA Essentials of Strength Training "
        "and Conditioning — https://www.nsca.com/example.\n",
        encoding="utf-8",
    )
    builder = PromptBuilder(tmp_path)
    assert builder.known_citation_urls() == {"https://www.nsca.com/example"}


class _StubOpenAI:
    """Minimal stand-in for CoachOpenAIClient, swapped onto CoachService.openai."""

    def __init__(self, text: str) -> None:
        self._text = text

    def respond(self, *, system_prompt: str, user_message: str) -> OpenAIResult:
        return OpenAIResult(text=self._text, metadata={"model": "stub"})


def test_answer_question_flags_unverified_citation_url(
    factory: ServiceFactory, caplog: pytest.LogCaptureFixture
) -> None:
    with factory.session() as session, caplog.at_level(logging.WARNING):
        coach = factory.coach_service(session)
        user = coach.get_user("123")
        coach.openai = _StubOpenAI(
            "Alternate push/pull movements — see https://example-not-real.test/study."
        )
        response = coach.answer_question(user.id, "Why alternate push and pull exercises?")

    assert response.metadata["unverified_citation_urls"] == [
        "https://example-not-real.test/study"
    ]
    assert "unverified" in caplog.text.lower()


def test_answer_question_does_not_flag_when_no_url_cited(factory: ServiceFactory) -> None:
    with factory.session() as session:
        coach = factory.coach_service(session)
        user = coach.get_user("123")
        coach.openai = _StubOpenAI("Alternate push and pull movements to avoid pre-fatigue.")
        response = coach.answer_question(user.id, "Why alternate push and pull exercises?")

    assert "unverified_citation_urls" not in response.metadata


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


def test_measurement_repository_latest_with_photo_excludes_photoless(
    factory: ServiceFactory,
) -> None:
    with factory.session() as session:
        user = factory.coach_service(session).get_user("123")
        repo = MeasurementEventRepository(session)
        older_with_photo = repo.add(
            models.MeasurementEvent(
                user_id=user.id,
                measured_at=datetime(2026, 2, 1, tzinfo=UTC),
                progress_photo_path="uploads/progress/2026-02-01/old.png",
            )
        )
        # Newer, but no photo - must not be returned in place of the older one that has one.
        repo.add(
            models.MeasurementEvent(
                user_id=user.id,
                measured_at=datetime(2026, 3, 1, tzinfo=UTC),
                progress_photo_path=None,
            )
        )
        latest = repo.latest_with_photo_for_user(user.id)

    assert latest is not None
    assert latest.id == older_with_photo.id


def test_process_progress_photo_without_previous_photo(
    factory: ServiceFactory, tmp_path: Path
) -> None:
    image_path = tmp_path / "physique.png"
    Image.new("RGB", (10, 10), color="white").save(image_path)

    with factory.session() as session:
        user = factory.coach_service(session).get_user("123")
        processor = factory.vision_processor(session)
        extraction = processor.process_progress_photo(user_id=user.id, source_path=image_path)

    assert extraction.kind == ImageKind.PROGRESS_PHOTO
    assert extraction.retained_path is not None
    assert Path(extraction.retained_path).exists()
    assert list((tmp_path / "uploads" / "tmp").glob("*")) == []


def test_process_progress_photo_preserves_previous_photo(
    factory: ServiceFactory, tmp_path: Path
) -> None:
    previous_path = tmp_path / "previous_progress.png"
    Image.new("RGB", (10, 10), color="blue").save(previous_path)
    previous_bytes = previous_path.read_bytes()

    new_image_path = tmp_path / "new_progress.png"
    Image.new("RGB", (10, 10), color="red").save(new_image_path)

    with factory.session() as session:
        user = factory.coach_service(session).get_user("123")
        processor = factory.vision_processor(session)
        extraction = processor.process_progress_photo(
            user_id=user.id,
            source_path=new_image_path,
            previous_photo_path=str(previous_path),
            previous_measured_at=datetime(2026, 7, 1, tzinfo=UTC),
        )

    # Critical invariant: the previous photo is someone else's permanent file.
    assert previous_path.exists()
    assert previous_path.read_bytes() == previous_bytes
    assert extraction.retained_path is not None
    assert Path(extraction.retained_path) != previous_path
    assert list((tmp_path / "uploads" / "tmp").glob("*")) == []


def test_progress_photo_extraction_surfaces_feedback_message(factory: ServiceFactory) -> None:
    extraction = VisionExtraction(
        kind=ImageKind.PROGRESS_PHOTO,
        confidence=0.9,
        needs_clarification=False,
        facts={"feedback": "Midsection looks slightly leaner through the waist since last time."},
        retained_path=None,
    )
    with factory.session() as session:
        coach = factory.coach_service(session)
        user = coach.get_user("123")
        response = coach.store_vision_extraction(user.id, extraction)

    assert response.metadata["event_type"] == "measurement_recorded"
    assert response.message == "Midsection looks slightly leaner through the waist since last time."


def test_cardio_screenshot_extraction_stores_structured_cardio(factory: ServiceFactory) -> None:
    extraction = VisionExtraction(
        kind=ImageKind.CARDIO_SCREENSHOT,
        confidence=0.9,
        needs_clarification=False,
        facts={
            "modality": "Indoor Run",
            "duration_minutes": 21,
            "distance_miles": 1.43,
            "calories_burned": 227,
            "average_heart_rate": 168,
        },
        retained_path=None,
    )
    with factory.session() as session:
        coach = factory.coach_service(session)
        user = coach.get_user("123")
        response = coach.store_vision_extraction(user.id, extraction)
        events = CardioEventRepository(session).recent(user.id)

    assert response.metadata["event_type"] == "cardio_completed"
    assert len(events) == 1
    assert events[0].modality == "Indoor Run"
    assert events[0].duration_minutes == 21
    assert events[0].distance_miles == 1.43
    assert events[0].calories_burned == 227
    assert response.message == "Logged: Indoor Run (21 min, 1.43 mi, 227 cal, 168 bpm avg)"


def test_vision_processor_processes_cardio_screenshot(
    factory: ServiceFactory, tmp_path: Path
) -> None:
    image_path = tmp_path / "cardio.png"
    Image.new("RGB", (10, 10), color="white").save(image_path)

    with factory.session() as session:
        user = factory.coach_service(session).get_user("123")
        processor = factory.vision_processor(session)
        extraction = processor.process_cardio_screenshots(
            user_id=user.id,
            source_paths=[image_path],
        )

    tmp_files = list((tmp_path / "uploads" / "tmp").glob("*"))
    assert extraction.kind == ImageKind.CARDIO_SCREENSHOT
    assert extraction.needs_clarification is True
    assert tmp_files == []


def test_prompt_builder_errors_for_missing_config(tmp_path: Path) -> None:
    builder = PromptBuilder(tmp_path)
    with pytest.raises(FileNotFoundError):
        builder.build("user")
