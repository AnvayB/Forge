from __future__ import annotations

from fitness_coach.routing.classifier import classify_message
from fitness_coach.routing.guidance import guidance_for


def test_full_body_request_gets_custom_split_guidance_not_default_schedule_text() -> None:
    decision = classify_message(
        "I want to aim for doing full body tomorrow but might not be able to do "
        "anything on Sunday because of other plans."
    )
    text = guidance_for(decision)
    assert "full body" in text
    assert "search_knowledge_base" in text
    # No fixed exercise list is baked in here - selection is left to the model.
    assert "lat pulldown" not in text.lower()
    # The default schedule guidance's "Follow any active plan override" line should
    # not leak in - the custom-split text fully replaces it.
    assert "Follow any active plan override" not in text


def test_custom_split_guidance_is_generic_across_labels() -> None:
    # Same template, just the label substituted in - no per-split branch/lookup table.
    push_text = guidance_for(classify_message("Can we do a push day tomorrow?"))
    pull_text = guidance_for(classify_message("I want to do a pull workout tonight")).replace(
        "pull", "push"
    )
    assert push_text == pull_text


def test_default_schedule_request_keeps_original_guidance() -> None:
    decision = classify_message("What should I train tonight?")
    text = guidance_for(decision)
    assert "Follow any active plan override" in text
