"""Per-route prompt guidance, kept as plain text so routing behavior stays inspectable."""

from __future__ import annotations

from fitness_coach.routing.classifier import RouteCategory, RoutingDecision

_GUIDANCE: dict[RouteCategory, str] = {
    RouteCategory.SAFETY: (
        "This message describes pain, discomfort, or an injury. Handle it conservatively:\n"
        "- If duration/severity/location is unclear, ask ONE targeted question before advising.\n"
        "- Call get_active_constraints (known injuries) and, if a specific exercise is involved, "
        "get_exercise_history for that lift so the answer reflects recent load.\n"
        "- For a new, mild, plausibly load-related complaint: give a concrete, specific "
        "modification (swap, grip, volume drop), not a reflexive 'rest and see a doctor'.\n"
        "- Recommend in-person evaluation when pain is recurring, worsening, sharp/acute, or "
        "involves numbness, tingling, swelling, instability, or chest symptoms - and say why "
        "this one crossed that line.\n"
        "- Never encourage training through joint pain. Do not use external web research here; "
        "ground guidance in the knowledge base or general consensus only.\n"
        "- If the user reports a new or worsening issue, call log_injury so it is remembered."
    ),
    RouteCategory.EXERCISE_HISTORY: (
        "This is about a specific lift's history. Call get_exercise_history BEFORE answering "
        "and base every number on the tool result - never estimate or recall a trend from "
        "memory. Report raw facts (weights, reps, dates) separately from your interpretation. "
        "If the history is too short to judge (fewer than 3 sessions), say so plainly."
    ),
    RouteCategory.NUTRITION_TARGETS: (
        "Answer from the configured targets via get_nutrition_targets. Do not compute averages "
        "or trends from nutrition history. Lead with the number; explain only if asked."
    ),
    RouteCategory.SCHEDULE: (
        "Use get_todays_plan for the scheduled split and get_active_constraints for injuries "
        "and overrides. Follow any active plan override instead of the default split. Format "
        "the workout as a bullet list (one exercise per line); keep everything else brief."
    ),
    RouteCategory.RECENT_ACTIVITY: (
        "Look up the events with get_recent_events (or get_exercise_history for a named lift) "
        "and report what was actually logged. Do not infer streaks, averages, or trends."
    ),
    RouteCategory.KNOWLEDGE: (
        "This is a general question. Answer from stable knowledge; call search_knowledge_base "
        "to ground any specific or surprising claim with an exact citation. If the knowledge "
        "base does not cover it and the topic is genuinely fast-moving or unfamiliar, call "
        "flag_for_external_research once; otherwise answer from consensus without inventing "
        "a citation. Do not pull the user's training history unless the question depends on it."
    ),
    RouteCategory.JUDGMENT: (
        "This is a coaching judgment call. Decide which evidence you actually need: personal "
        "data (get_exercise_history, get_recent_events, get_active_constraints), stable "
        "knowledge (search_knowledge_base), or - only if the knowledge base has no coverage - "
        "flag_for_external_research. Fetch only what changes the recommendation. Keep retrieved "
        "fact, general principle, and your recommendation distinguishable, then end with one "
        "practical action."
    ),
    RouteCategory.CONVERSATIONAL: (
        "This is conversational. Reply in one short sentence; no tools, no data, no advice "
        "unless the recent conversation makes one obviously useful."
    ),
    RouteCategory.LOCKED_ANALYTICS: "",
}


def guidance_for(decision: RoutingDecision) -> str:
    """Return the routing-guidance prompt section for a decision (may be empty)."""

    text = _GUIDANCE.get(decision.category, "")
    if not text:
        return ""
    header = f"# Routing Guidance (route: {decision.category.value})\n\n"
    tools = ", ".join(decision.tools) if decision.tools else "none"
    return f"{header}{text}\n\nTools available for this reply: {tools}."
