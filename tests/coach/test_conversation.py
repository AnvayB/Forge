from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fitness_coach.coach.conversation import ConversationWindow


def test_window_keeps_recent_turns_and_expires() -> None:
    window = ConversationWindow(max_turns=3, ttl=timedelta(hours=2))
    t0 = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    window.append("u", "user", "My elbow hurts when I curl", now=t0)
    window.append("u", "assistant", "How long has that been going on?", now=t0)
    window.append("u", "user", "About a week", now=t0 + timedelta(minutes=1))
    window.append("u", "assistant", "Drop curls for a few days", now=t0 + timedelta(minutes=1))

    turns = window.recent("u", now=t0 + timedelta(minutes=2))
    assert [t.text for t in turns] == [
        "How long has that been going on?",
        "About a week",
        "Drop curls for a few days",
    ]
    assert "Coach: About a week" not in window.format("u")
    assert window.recent("u", now=t0 + timedelta(hours=3)) == []
    assert window.format("u") == ""


def test_window_is_per_user() -> None:
    window = ConversationWindow()
    window.append("a", "user", "hi")
    assert window.recent("b") == []
    window.clear("a")
    assert window.recent("a") == []
