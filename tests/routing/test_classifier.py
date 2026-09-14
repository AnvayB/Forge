from __future__ import annotations

import pytest

from fitness_coach.routing.classifier import (
    ALL_CHAT_TOOLS,
    TOOL_EXERCISE_HISTORY,
    TOOL_EXTERNAL_RESEARCH,
    TOOL_NUTRITION_TARGETS,
    TOOL_TODAYS_PLAN,
    RouteCategory,
    classify_message,
)


@pytest.mark.parametrize(
    ("message", "category"),
    [
        ("My elbow hurts when I curl.", RouteCategory.SAFETY),
        ("Sharp pain in my shoulder on incline press", RouteCategory.SAFETY),
        ("my knee is sore after leg press, normal?", RouteCategory.SAFETY),
        ("Should I see a doctor about my wrist?", RouteCategory.SAFETY),
        ("Why has my bench not progressed in 5 weeks?", RouteCategory.EXERCISE_HISTORY),
        ("How has my squat been trending?", RouteCategory.EXERCISE_HISTORY),
        ("What did I do on leg press last time?", RouteCategory.EXERCISE_HISTORY),
        ("Show me my monthly analytics trend", RouteCategory.LOCKED_ANALYTICS),
        ("What's my workout streak?", RouteCategory.LOCKED_ANALYTICS),
        ("How consistent have I been this month?", RouteCategory.LOCKED_ANALYTICS),
        ("How much protein should I eat?", RouteCategory.NUTRITION_TARGETS),
        ("What are my macro targets?", RouteCategory.NUTRITION_TARGETS),
        ("What should I train tonight?", RouteCategory.SCHEDULE),
        ("What's on today?", RouteCategory.SCHEDULE),
        ("Is today a rest day?", RouteCategory.SCHEDULE),
        ("What did I do yesterday?", RouteCategory.RECENT_ACTIVITY),
        ("How did I sleep last night?", RouteCategory.RECENT_ACTIVITY),
        ("Should I switch from incline dumbbell press to barbell?", RouteCategory.JUDGMENT),
        ("Should I change my cardio?", RouteCategory.JUDGMENT),
        ("Is it worth adding a second back exercise?", RouteCategory.JUDGMENT),
        ("Why do we do incline before flat bench?", RouteCategory.KNOWLEDGE),
        ("Does training to failure build more muscle?", RouteCategory.KNOWLEDGE),
        ("What's the difference between RIR and RPE?", RouteCategory.KNOWLEDGE),
        ("thanks!", RouteCategory.CONVERSATIONAL),
        ("ok sounds good", RouteCategory.CONVERSATIONAL),
    ],
)
def test_routes(message: str, category: RouteCategory) -> None:
    assert classify_message(message).category == category


def test_safety_overrides_other_routes_and_blocks_external_research() -> None:
    decision = classify_message("Should I keep benching if my shoulder hurts?")
    assert decision.category == RouteCategory.SAFETY
    assert decision.research_allowed is False
    assert TOOL_EXTERNAL_RESEARCH not in decision.tools
    assert decision.entities["body_areas"] == ["shoulder"]


def test_red_flags_are_surfaced_as_entities() -> None:
    decision = classify_message("I felt dizzy and my chest hurt during cardio")
    assert decision.category == RouteCategory.SAFETY
    assert decision.entities["severity_hint"] == "high"
    assert "dizzy" in decision.entities["red_flags"]


def test_named_lift_history_is_allowed_under_analytics_lock() -> None:
    decision = classify_message("What's my average bench weight lately?", analytics_locked=True)
    assert decision.category == RouteCategory.EXERCISE_HISTORY
    assert TOOL_EXERCISE_HISTORY in decision.tools
    assert decision.entities["exercises"] == ["bench"]
    assert decision.entities.get("window_days") is None


def test_exercise_window_is_extracted() -> None:
    decision = classify_message("Why has my bench not progressed in 5 weeks?")
    assert decision.entities["window_days"] == 35


def test_lock_can_be_disabled() -> None:
    assert classify_message("What's my workout streak?", analytics_locked=False).category != (
        RouteCategory.LOCKED_ANALYTICS
    )


def test_locked_analytics_exposes_no_tools() -> None:
    decision = classify_message("How's my adherence been over the last month?")
    assert decision.category == RouteCategory.LOCKED_ANALYTICS
    assert decision.tools == ()


def test_nutrition_targets_do_not_touch_history() -> None:
    decision = classify_message("How much protein should I eat?")
    assert decision.needs_user_history is False
    assert decision.tools == (TOOL_NUTRITION_TARGETS, "search_knowledge_base")
    assert TOOL_EXERCISE_HISTORY not in decision.tools


def test_knowledge_questions_do_not_expose_history_tools() -> None:
    decision = classify_message("Why do we do incline before flat bench?")
    assert decision.needs_user_history is False
    assert TOOL_EXERCISE_HISTORY not in decision.tools
    assert decision.research_allowed is True


def test_schedule_route_uses_plan_tool() -> None:
    decision = classify_message("What should I train tonight?")
    assert TOOL_TODAYS_PLAN in decision.tools
    assert decision.research_allowed is False


def test_judgment_gets_full_chat_tool_set() -> None:
    decision = classify_message("Should I change my cardio?")
    assert decision.tools == ALL_CHAT_TOOLS
    assert decision.needs_conversation_context is True


def test_no_chat_tool_computes_cumulative_analytics() -> None:
    forbidden = ("streak", "adherence", "average", "trend", "summary", "metrics", "report")
    for tool in ALL_CHAT_TOOLS:
        assert not any(word in tool for word in forbidden), tool


def test_paraphrases_route_consistently() -> None:
    variants = [
        "Why hasn't my bench moved in a month?",
        "My bench has been stuck for weeks, why?",
        "bench press plateau - what's going on with my numbers",
    ]
    assert {classify_message(v).category for v in variants} == {RouteCategory.EXERCISE_HISTORY}


def test_decision_serializes() -> None:
    payload = classify_message("What should I train tonight?").as_dict()
    assert payload["category"] == "schedule"
    assert isinstance(payload["tools"], list)
