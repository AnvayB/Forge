"""The sentence closing a workout confirmation: grounded in working sets, never filler."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from evals.response_fixtures import load_baseline, reply_stats, workout_reply
from fitness_coach.coach.factory import ServiceFactory
from fitness_coach.config.settings import AppSettings, CoachSettings
from fitness_coach.database.schemas import WorkoutLog
from fitness_coach.vision.processor import ImageKind, VisionExtraction

START = datetime(2026, 1, 5, tzinfo=UTC)


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


def _sets(*pairs: tuple[float, int]) -> list[dict[str, float]]:
    return [{"weight": weight, "reps": reps} for weight, reps in pairs]


def _reply_after(
    factory: ServiceFactory, prior: list[list[dict[str, float]]], today: list[dict[str, float]]
) -> str:
    """Log ``prior`` sessions of one exercise (oldest first), then ``today``'s; return the reply."""

    exercise = "Lat Pulldown"
    with factory.session() as session:
        coach = factory.coach_service(session)
        user_id = coach.get_user("123").id
        for week, sets in enumerate(prior):
            coach.log_workout(
                user_id,
                WorkoutLog(
                    occurred_at=START + timedelta(weeks=week),
                    workout_type="Pull",
                    exercises=[{"name": exercise, "sets": sets}],
                ),
            )
        extraction = VisionExtraction(
            kind=ImageKind.WORKOUT_SCREENSHOT,
            confidence=0.95,
            needs_clarification=False,
            facts={"workout_type": "Pull", "exercises": [{"name": exercise, "sets": today}]},
            retained_path=None,
        )
        return coach.store_vision_extraction(
            user_id, extraction, now=START + timedelta(weeks=len(prior))
        ).message


def _closing_line(reply: str) -> str:
    return reply.strip().splitlines()[-1] if reply.strip() else ""


def test_a_warmup_is_never_mistaken_for_the_top_set(factory: ServiceFactory) -> None:
    # An old heavy session keeps the all-time best out of reach, so no PR muddies the line.
    prior = [_sets((45, 5)), _sets((30, 5)), _sets((30, 5))]
    # 24x20 has a higher estimated max than 30x3, but it is a warm-up before the top set.
    today = _sets((24, 20), (30, 3))

    reply = _reply_after(factory, prior, today)

    closing = _closing_line(reply)
    assert "30lbs x3" in closing
    assert "down from 30lbs x5" in closing
    assert "24lbs" not in closing


def test_comparison_uses_the_five_most_recent_sessions_not_the_oldest(
    factory: ServiceFactory,
) -> None:
    old, recent = _sets((100, 5)), _sets((60, 5))
    prior = [old] * 4 + [recent] * 5

    reply = _reply_after(factory, prior, _sets((62, 5)))

    closing = _closing_line(reply)
    assert "up from 60lbs x5" in closing
    assert "down from" not in closing


def test_an_unchanged_top_set_is_reported_as_steady_not_as_news(factory: ServiceFactory) -> None:
    prior = [_sets((100, 8))] * 3

    reply = _reply_after(factory, prior, _sets((100, 8)))

    assert "held steady" in _closing_line(reply).lower()


def test_exercises_already_celebrated_as_prs_are_not_repeated(factory: ServiceFactory) -> None:
    prior = [_sets((100, 8))] * 3

    reply = _reply_after(factory, prior, _sets((110, 8)))

    assert "New PR" in reply
    assert reply.count("Lat Pulldown") == 2  # the logged line and the PR line, nothing more


def test_first_ever_workout_gets_no_filler_sentence(factory: ServiceFactory) -> None:
    reply = _reply_after(factory, [], _sets((100, 8)))

    assert "Solid session" not in reply
    assert "not enough" not in reply
    assert reply.strip().splitlines()[-1].startswith("- Lat Pulldown")


def test_canned_rep_range_filler_is_gone_and_reply_is_no_longer_than_the_baseline(
    factory: ServiceFactory,
) -> None:
    reply = workout_reply(factory)

    assert "productive rep range" not in reply
    assert "recent baseline" not in reply
    assert reply_stats(reply)["words"] <= load_baseline()["workout_confirmation"]["words"]


def test_next_time_line_appears_only_when_a_weight_change_is_earned(
    factory: ServiceFactory,
) -> None:
    # Third session in a row at the top of the compound range (10 reps): earn a small increase.
    reply = _reply_after(factory, [_sets((100, 10))] * 2, _sets((100, 10)))

    line = next(line for line in reply.splitlines() if line.startswith("Next time"))
    assert "Lat Pulldown" in line and "105lbs" in line
    assert "1rm" not in line.lower() and "max" not in line.lower()
    assert sum(1 for entry in reply.splitlines() if entry.startswith("Next time")) == 1


def test_one_good_session_does_not_prompt_a_weight_increase(factory: ServiceFactory) -> None:
    reply = _reply_after(factory, [_sets((100, 8))] * 2, _sets((100, 10)))

    assert "Next time" not in reply


def test_next_time_line_does_not_appear_for_a_normal_log(factory: ServiceFactory) -> None:
    reply = workout_reply(factory)

    assert "Next time" not in reply
