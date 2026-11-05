from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from fitness_coach.bot.discord_bot import _reply_in_chunks, _send_progress_charts, build_bot
from fitness_coach.coach.factory import ServiceFactory
from fitness_coach.config.settings import AppSettings, CoachSettings
from fitness_coach.database import models
from fitness_coach.database.repositories import ProgressReviewRepository, WorkoutEventRepository
from fitness_coach.vision.processor import ImageKind, VisionExtraction


@pytest.fixture
def factory(tmp_path: Path) -> ServiceFactory:
    result = ServiceFactory(
        AppSettings(
            database_url=f"sqlite:///{tmp_path / 'bot.db'}",
            config_dir=Path("config"),
            uploads_dir=tmp_path / "uploads",
        ),
        CoachSettings(preferred_model="test-model"),
    )
    yield result
    result.engine.dispose()


class _Context:
    def __init__(self, attachments: list[object] | None = None) -> None:
        self.author = SimpleNamespace(id=1234)
        self.message = SimpleNamespace(id=5678, attachments=attachments or [])
        self.replies: list[str] = []
        self.sent: list[dict[str, object]] = []

    async def reply(self, content: str) -> None:
        self.replies.append(content)

    async def send(self, content: str | None = None, **kwargs: object) -> None:
        self.sent.append({"content": content, **kwargs})
        for upload in kwargs.get("files", []):
            upload.close()


def _logged_extraction() -> VisionExtraction:
    return VisionExtraction(
        kind=ImageKind.WORKOUT_TEXT,
        confidence=0.95,
        needs_clarification=False,
        facts={
            "workout_type": "Upper",
            "duration_minutes": 45,
            "exercises": [
                {"name": "Bench Press", "sets": [{"weight": 135, "reps": 8}]}
            ],
        },
        retained_path=None,
    )


def test_workout_command_logs_typed_sets_and_replies(factory: ServiceFactory) -> None:
    class Processor:
        def process_workout_text(self, *, user_id: str, text: str) -> VisionExtraction:
            assert text == "Upper, bench 135 x 8"
            return _logged_extraction()

    factory.vision_processor = lambda _session: Processor()  # type: ignore[method-assign]
    bot = build_bot(factory)
    ctx = _Context()

    asyncio.run(bot.get_command("workout").callback(ctx, summary="Upper, bench 135 x 8"))

    assert ctx.replies and "Logged: Upper (45 min)" in ctx.replies[0]
    with factory.session() as session:
        user = session.query(models.User).one()
        events = WorkoutEventRepository(session).recent(user.id)
    assert len(events) == 1
    assert events[0].exercises[0]["sets"] == [{"weight": 135, "reps": 8}]
    asyncio.run(bot.close())


def test_critical_commands_are_registered(factory: ServiceFactory) -> None:
    bot = build_bot(factory)

    assert {
        "checkin",
        "workout",
        "cardio",
        "nutrition",
        "sleep",
        "progress",
        "lastreview",
        "recent",
    } <= set(bot.all_commands)
    asyncio.run(bot.close())


def test_long_discord_reply_is_split_at_message_limit() -> None:
    ctx = _Context()

    asyncio.run(_reply_in_chunks(ctx, "x" * 4500))

    parts = ctx.replies + [entry["content"] for entry in ctx.sent if entry.get("content")]
    assert len(parts) >= 3
    assert all(isinstance(part, str) and len(part) <= 2000 for part in parts)
    assert "".join(parts) == "x" * 4500


def test_chart_files_and_empty_output_directory_are_cleaned(
    tmp_path: Path,
) -> None:
    ctx = _Context()
    uploads = tmp_path / "uploads"
    chart_dir = uploads / "tmp" / "charts_5678"

    asyncio.run(
        _send_progress_charts(
            ctx,
            {"workouts": {"volume_by_exercise": {"bench press": 12000}}},
            uploads,
        )
    )

    assert len(ctx.sent) == 1
    assert chart_dir.exists() is False
    assert list((uploads / "tmp").glob("charts_*")) == []


def test_workout_screenshot_command_saves_processes_and_cleans_attachments(
    factory: ServiceFactory, tmp_path: Path
) -> None:
    class Attachment:
        id = 77
        filename = "proof.PNG"
        content_type = "image/png"

        async def save(self, path: Path) -> None:
            Image.new("RGB", (8, 8), color="white").save(path)

    class Processor:
        def process_workout_screenshots(
            self, *, user_id: str, source_paths: list[Path], extra_notes: str
        ) -> VisionExtraction:
            assert len(source_paths) == 1 and source_paths[0].exists()
            assert extra_notes == ""
            return _logged_extraction()

    factory.vision_processor = lambda _session: Processor()  # type: ignore[method-assign]
    bot = build_bot(factory)
    ctx = _Context([Attachment()])

    asyncio.run(bot.get_command("workout").callback(ctx))

    assert ctx.replies and "Logged: Upper (45 min)" in ctx.replies[0]
    assert list((tmp_path / "uploads" / "tmp").glob("discord_*")) == []
    with factory.session() as session:
        user = session.query(models.User).one()
        assert len(WorkoutEventRepository(session).recent(user.id)) == 1
    asyncio.run(bot.close())


def test_workout_screenshot_cleanup_runs_when_processing_fails(
    factory: ServiceFactory, tmp_path: Path
) -> None:
    class Attachment:
        id = 78
        filename = "broken.png"
        content_type = "image/png"

        async def save(self, path: Path) -> None:
            path.write_bytes(b"partial upload")

    class Processor:
        def process_workout_screenshots(self, **_kwargs: object) -> VisionExtraction:
            raise RuntimeError("processor failed")

    factory.vision_processor = lambda _session: Processor()  # type: ignore[method-assign]
    bot = build_bot(factory)
    ctx = _Context([Attachment()])

    with pytest.raises(RuntimeError, match="processor failed"):
        asyncio.run(bot.get_command("workout").callback(ctx))

    assert list((tmp_path / "uploads" / "tmp").glob("discord_*")) == []
    asyncio.run(bot.close())


def test_lastreview_redelivers_persisted_review(factory: ServiceFactory) -> None:
    with factory.session() as session:
        coach = factory.coach_service(session)
        user = coach.get_user("1234")
        ProgressReviewRepository(session).add(
            models.ProgressReview(
                user_id=user.id,
                period_start=datetime(2026, 6, 1, tzinfo=UTC),
                period_end=datetime(2026, 7, 1, tzinfo=UTC),
                metrics={},
                narrative="Your saved review is ready.",
            )
        )

    bot = build_bot(factory)
    ctx = _Context()
    asyncio.run(bot.get_command("lastreview").callback(ctx))

    assert ctx.replies == ["Your saved review is ready."]
    assert not list((factory.app_settings.uploads_dir / "tmp").glob("charts_*"))
    asyncio.run(bot.close())


def test_free_text_handler_replies_when_service_raises(factory: ServiceFactory) -> None:
    def fail(_session):
        raise RuntimeError("service unavailable")

    factory.coach_service = fail  # type: ignore[method-assign]
    bot = build_bot(factory)
    bot.process_commands = lambda _message: asyncio.sleep(0)  # type: ignore[method-assign]
    message = SimpleNamespace(
        author=SimpleNamespace(id=1234, bot=False),
        content="What should I train today?",
        attachments=[],
        replies=[],
    )
    async def reply(content: str) -> None:
        message.replies.append(content)

    message.reply = reply
    asyncio.run(bot.on_message(message))

    assert message.replies == ["Something went wrong processing that message."]
    asyncio.run(bot.close())


def _log_press_session(factory: ServiceFactory) -> str:
    """Log one session where set 1 (40x5) is a real working set the user may re-tag."""

    extraction = VisionExtraction(
        kind=ImageKind.WORKOUT_TEXT,
        confidence=0.95,
        needs_clarification=False,
        facts={
            "workout_type": "Upper",
            "exercises": [
                {
                    "name": "Incline Dumbbell Press",
                    "sets": [{"weight": 40, "reps": 5}, {"weight": 40, "reps": 8}],
                },
                {"name": "Lat Pulldown", "sets": [{"weight": 100, "reps": 10}]},
            ],
        },
        retained_path=None,
    )
    with factory.session() as session:
        coach = factory.coach_service(session)
        user = coach.get_user("1234")
        coach.store_vision_extraction(user.id, extraction)
        return user.id


def _run_setfix(factory: ServiceFactory, args: str) -> str:
    bot = build_bot(factory)
    ctx = _Context()
    asyncio.run(bot.get_command("setfix").callback(ctx, args=args))
    asyncio.run(bot.close())
    assert len(ctx.replies) == 1, "setfix must answer with exactly one message"
    return ctx.replies[0]


def _stored_sets(factory: ServiceFactory, user_id: str, exercise_index: int = 0) -> list[dict]:
    with factory.session() as session:
        latest = WorkoutEventRepository(session).recent(user_id)[0]
        return [dict(item) for item in latest.exercises[exercise_index]["sets"]]


def test_setfix_retags_sets_in_the_latest_workout_and_persists(factory: ServiceFactory) -> None:
    user_id = _log_press_session(factory)

    reply = _run_setfix(factory, "incline press 1 warmup")

    assert "Incline Dumbbell Press: (40lbs x5) 40lbs x8" in reply
    assert _stored_sets(factory, user_id)[0]["set_type"] == "warmup"
    assert "set_type" not in _stored_sets(factory, user_id)[1]


def test_setfix_accepts_ranges_and_short_type_names(factory: ServiceFactory) -> None:
    user_id = _log_press_session(factory)

    _run_setfix(factory, "incline press 1-2 bo")

    assert [item["set_type"] for item in _stored_sets(factory, user_id)] == ["backoff", "backoff"]


def test_setfix_changes_working_volume_but_not_what_was_logged(factory: ServiceFactory) -> None:
    from fitness_coach.analytics.strength import exercise_volume, working_volume

    user_id = _log_press_session(factory)
    _run_setfix(factory, "incline press 1 warmup")

    with factory.session() as session:
        exercise = WorkoutEventRepository(session).recent(user_id)[0].exercises[0]
        assert exercise_volume(exercise) == 40 * 5 + 40 * 8
        assert working_volume(exercise) == 40 * 8
        assert [(s["weight"], s["reps"]) for s in exercise["sets"]] == [(40, 5), (40, 8)]


@pytest.mark.parametrize(
    "args",
    [
        "incline press 9 warmup",
        "incline press 0 warmup",
        "incline press 1 dropset",
        "squat 1 warmup",
        "incline press",
        "",
    ],
)
def test_setfix_bad_input_gets_one_line_and_changes_nothing(
    factory: ServiceFactory, args: str
) -> None:
    user_id = _log_press_session(factory)
    before = _stored_sets(factory, user_id)

    reply = _run_setfix(factory, args)

    assert "\n" not in reply.strip()
    assert _stored_sets(factory, user_id) == before


def test_setfix_with_no_logged_workout_says_so(factory: ServiceFactory) -> None:
    reply = _run_setfix(factory, "incline press 1 warmup")

    assert "no workout" in reply.lower()
