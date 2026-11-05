from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from fitness_coach.coach.factory import ServiceFactory
from fitness_coach.config.settings import AppSettings, CoachSettings
from fitness_coach.database.repositories import WorkoutEventRepository
from fitness_coach.vision.processor import ImageKind, VisionExtraction


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


def _extraction(exercises: list[dict[str, Any]]) -> VisionExtraction:
    return VisionExtraction(
        kind=ImageKind.WORKOUT_SCREENSHOT,
        confidence=0.95,
        needs_clarification=False,
        facts={"workout_type": "Upper", "exercises": exercises},
        retained_path=None,
    )


def _log(factory: ServiceFactory, exercises: list[dict[str, Any]]):
    with factory.session() as session:
        coach = factory.coach_service(session)
        user = coach.get_user("123")
        response = coach.store_vision_extraction(user.id, _extraction(exercises))
        stored = WorkoutEventRepository(session).recent(user.id)[0].exercises
    return response, stored


def _sets(*pairs: tuple[float, int]) -> list[dict[str, float]]:
    return [{"weight": weight, "reps": reps} for weight, reps in pairs]


def test_right_and_left_rows_are_stored_as_one_two_sided_set(factory: ServiceFactory) -> None:
    rows = [
        {"weight": 25, "reps": 10, "side": "R"},
        {"weight": 25, "reps": 10, "side": "L"},
    ]

    _, stored = _log(factory, [{"name": "Dumbbell Shoulder Press", "sets": rows}])

    assert stored[0]["sets"] == [{"weight": 25, "reps": 10, "sides": 2}]


def test_confirmation_puts_warmups_in_parentheses_and_lighter_back_offs_after_an_arrow(
    factory: ServiceFactory,
) -> None:
    exercise = {
        "name": "Incline Press",
        "sets": _sets((20, 10), (25, 8), (30, 6), (40, 5), (35, 8), (35, 8)),
    }

    response, _ = _log(factory, [exercise])

    assert (
        "- Incline Press: (20lbs x10, 25lbs x8, 30lbs x6) 40lbs x5 → 35lbs x8 ×2"
        in response.message
    )


def test_confirmation_is_unchanged_when_there_is_nothing_to_mark(
    factory: ServiceFactory,
) -> None:
    exercise = {"name": "Bench Press", "sets": _sets((185, 5), (185, 5), (185, 5))}

    response, _ = _log(factory, [exercise])

    assert "- Bench Press: 185lbs x5 ×3" in response.message
    assert "(" not in response.message
    assert "→" not in response.message


def test_explicit_warmup_tag_is_stored_and_shown(factory: ServiceFactory) -> None:
    sets = [
        {"weight": 60, "reps": 12, "set_type": "warmup"},
        {"weight": 70, "reps": 8},
    ]

    response, stored = _log(factory, [{"name": "Cable Row", "sets": sets}])

    assert stored[0]["sets"][0]["set_type"] == "warmup"
    assert "- Cable Row: (60lbs x12) 70lbs x8" in response.message


class _CapturingOpenAI:
    """Fakes the OpenAI client boundary and records the task it was asked to perform."""

    def __init__(self) -> None:
        self.tasks: list[str] = []

    def _result(self):
        from fitness_coach.coach.openai_client import OpenAIResult

        return OpenAIResult(text='{"confidence": 0.9, "needs_clarification": false, "facts": {}}')

    def analyze_images(self, *, system_prompt, image_paths, task):
        self.tasks.append(task)
        return self._result()

    def analyze_text(self, *, system_prompt, task, text):
        self.tasks.append(task)
        return self._result()


def test_screenshot_extraction_asks_for_right_left_sides(
    factory: ServiceFactory, tmp_path: Path
) -> None:
    from PIL import Image

    image = tmp_path / "arrow.png"
    Image.new("RGB", (8, 8), color="white").save(image)
    stub = _CapturingOpenAI()
    with factory.session() as session:
        user = factory.coach_service(session).get_user("123")
        processor = factory.vision_processor(session)
        processor.openai = stub  # type: ignore[assignment]
        processor.process_workout_screenshots(user_id=user.id, source_paths=[image])

    assert "`side`" in stub.tasks[0]
    assert '"R"' in stub.tasks[0] and '"L"' in stub.tasks[0]


def test_typed_workout_extraction_asks_for_explicit_set_type_tags_only(
    factory: ServiceFactory,
) -> None:
    stub = _CapturingOpenAI()
    with factory.session() as session:
        user = factory.coach_service(session).get_user("123")
        processor = factory.vision_processor(session)
        processor.openai = stub  # type: ignore[assignment]
        processor.process_workout_text(user_id=user.id, text="curl: 20x10 w, 30x8")

    task = stub.tasks[0]
    assert "`set_type`" in task
    assert all(value in task for value in ("warmup", "working", "backoff"))
    assert "only" in task.lower()
