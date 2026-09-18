from __future__ import annotations

from fitness_coach.routing.classifier import classify_message
from fitness_coach.routing.guidance import guidance_for


def test_full_body_request_gets_custom_split_guidance_not_default_schedule_text() -> None:
    decision = classify_message(
        "I want to aim for doing full body tomorrow but might not be able to do "
        "anything on Sunday because of other plans."
    )
    text = guidance_for(decision)
    assert "fresh session" in text
    assert "bicep and a tricep" in text
    # The default schedule guidance's "Follow any active plan override" line should
    # not leak in - the custom-split text fully replaces it.
    assert "Follow any active plan override" not in text


def test_default_schedule_request_keeps_original_guidance() -> None:
    decision = classify_message("What should I train tonight?")
    text = guidance_for(decision)
    assert "Follow any active plan override" in text
