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

def _custom_split_guidance(label: str) -> str:
    """Guidance for an explicit ad-hoc split request (`requested_split` entity).

    Deliberately has no per-split branch or fixed exercise list - which exercises belong
    in a "push day" vs an "abs workout" is a programming judgment call for the model to
    make from real training principles and the knowledge base, not a lookup table baked
    into this file. The router only detected that the user wants a specific, named session
    instead of their default scheduled split; the content of that session is the model's
    job, same as any other coaching-judgment reply.
    """

    readable = label.replace("_", " ")
    return (
        f"The user asked for a '{readable}' session, not their default scheduled split. "
        "get_todays_plan only tells you whether it's a rest/cardio day, active overrides, "
        "and the last logged workout - do NOT just recite that day's scheduled exercise "
        "list, and do not take one existing day (e.g. 'Upper') and bolt on one extra "
        "exercise to approximate the request. Design a genuine session for "
        f"'{readable}': using standard exercise-science program design, work out which "
        "muscle groups a session with that name is actually expected to train and give "
        "each of them real, balanced coverage - don't default to whichever exercise is "
        "easiest to name. If the label is ambiguous or you're not confident what it "
        "conventionally covers, call search_knowledge_base to check training consensus "
        "before guessing. Prefer exercises already in the 'Favorite Exercises' list in "
        "training_preferences.md. Where that list doesn't adequately cover a muscle group "
        "this session needs, also call search_knowledge_base and bring in a well-supported "
        "exercise to fill the gap, and say plainly when something is a new suggestion "
        "rather than an existing favorite - never invent a citation to justify it. Order "
        "compound movements before isolation work. Keep per-movement volume sensible for "
        "a single ad-hoc session (lighter than a dedicated split day), and check "
        "get_recent_events so this doesn't stack on top of the same muscles already "
        "trained hard in the last day or two. Drop anything on 'Exercises to Avoid' or "
        "flagged by get_active_constraints."
    )


def guidance_for(decision: RoutingDecision) -> str:
    """Return the routing-guidance prompt section for a decision (may be empty)."""

    requested_split = decision.entities.get("requested_split")
    text = _custom_split_guidance(requested_split) if requested_split else ""
    text = text or _GUIDANCE.get(decision.category, "")
    if not text:
        return ""
    header = f"# Routing Guidance (route: {decision.category.value})\n\n"
    tools = ", ".join(decision.tools) if decision.tools else "none"
    return f"{header}{text}\n\nTools available for this reply: {tools}."
