"""Deterministic pre-classifier that runs before any LLM call.

The classifier answers one question per message: which information sources does a
good reply actually need? It never generates text. Safety- and policy-critical routing
(pain reports, the cumulative-analytics lock) is decided here by pattern matching so it
can never depend on model discretion; genuinely ambiguous coaching questions fall
through to the JUDGMENT category, where the LLM chooses among a bounded, chat-safe tool
set at generation time.

Cumulative analytics (streaks, adherence %, multi-week averages) are locked to the
progress-review cadence. That lock is enforced structurally: no tool that computes them
is ever listed in any category's `tools`, so no phrasing can reach that data from chat.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

# Chat-safe tool names. Cumulative analytics helpers are deliberately absent.
TOOL_RECENT_EVENTS = "get_recent_events"
TOOL_EXERCISE_HISTORY = "get_exercise_history"
TOOL_ACTIVE_CONSTRAINTS = "get_active_constraints"
TOOL_TODAYS_PLAN = "get_todays_plan"
TOOL_NUTRITION_TARGETS = "get_nutrition_targets"
TOOL_SEARCH_KB = "search_knowledge_base"
TOOL_EXTERNAL_RESEARCH = "flag_for_external_research"
TOOL_REMEMBER_FACT = "remember_fact"
TOOL_LOG_INJURY = "log_injury"

ALL_CHAT_TOOLS: tuple[str, ...] = (
    TOOL_RECENT_EVENTS,
    TOOL_EXERCISE_HISTORY,
    TOOL_ACTIVE_CONSTRAINTS,
    TOOL_TODAYS_PLAN,
    TOOL_NUTRITION_TARGETS,
    TOOL_SEARCH_KB,
    TOOL_EXTERNAL_RESEARCH,
    TOOL_REMEMBER_FACT,
    TOOL_LOG_INJURY,
)


class RouteCategory(StrEnum):
    """Intent classes, ordered by matcher priority (safety always wins)."""

    SAFETY = "safety"
    LOCKED_ANALYTICS = "locked_analytics"
    EXERCISE_HISTORY = "exercise_history"
    NUTRITION_TARGETS = "nutrition_targets"
    SCHEDULE = "schedule"
    RECENT_ACTIVITY = "recent_activity"
    KNOWLEDGE = "knowledge"
    JUDGMENT = "judgment"
    CONVERSATIONAL = "conversational"


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    """Inspectable routing output: what the reply needs and which tools are exposed."""

    category: RouteCategory
    needs_conversation_context: bool
    needs_user_history: bool
    needs_calculations: bool
    research_allowed: bool
    stable_knowledge: bool
    tools: tuple[str, ...]
    matched_terms: tuple[str, ...] = ()
    entities: dict[str, object] = field(default_factory=dict)
    reason: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "category": self.category.value,
            "needs_conversation_context": self.needs_conversation_context,
            "needs_user_history": self.needs_user_history,
            "needs_calculations": self.needs_calculations,
            "research_allowed": self.research_allowed,
            "stable_knowledge": self.stable_knowledge,
            "tools": list(self.tools),
            "matched_terms": list(self.matched_terms),
            "entities": dict(self.entities),
            "reason": self.reason,
        }


# --- lexicons -------------------------------------------------------------------------

_BODY_AREAS = (
    "shoulder",
    "knee",
    "elbow",
    "wrist",
    "back",
    "lower back",
    "neck",
    "hip",
    "ankle",
    "forearm",
    "hamstring",
    "quad",
    "calf",
    "chest",
    "rotator cuff",
    "spine",
    "groin",
    "glute",
    "achilles",
    "pec",
    "bicep",
    "tricep",
)

# Word-bounded on purpose: substring matching would catch "numb" inside "numbers".
_PAIN_PATTERN = re.compile(
    r"\b(hurt|hurts|hurting|pain|pains|painful|ache|aches|aching|achy|injury|injured|injuries|"
    r"numb|numbness|tingling|tingles|tingly|dizzy|dizziness|faint|fainted|sharp|pinch|pinched|"
    r"pinching|tweak|tweaked|strain|strained|sprain|sprained|swollen|swelling|popped|popping|"
    r"clicking|clicks|impingement|impinged|twinge|stabbing|tore|torn|locked up|gave out|"
    r"chest pain|short of breath|shortness of breath|burning sensation|can'?t straighten|"
    r"can'?t put weight|discomfort|uncomfortable|bothers|bothering|flare[sd]? up|flared)\b",
    re.IGNORECASE,
)

# "hurt my gains/progress" is not a pain report.
_HURT_ABSTRACT_PATTERN = re.compile(
    r"\bhurts?\b\s+(my\s+|the\s+)?(lifting|lifts|gains|progress|progression|recovery|performance|"
    r"strength|numbers|results|workout|workouts|session|training|hypertrophy|growth|muscle growth|"
    r"cardio|sleep|adherence|consistency|chances|goals?)\b"
)

# Generic "sore" is normal post-training; it only counts near a body-part word.
_BODY_AREA_ALT = "|".join(re.escape(area) for area in _BODY_AREAS)
_JOINT_SORE_PATTERN = re.compile(
    rf"\b({_BODY_AREA_ALT})s?\b[^.?!]{{0,25}}\bsore\b|\bsore\b[^.?!]{{0,25}}\b({_BODY_AREA_ALT})s?\b"
)

# "what's going on with my shoulder" - recalling a known issue, no pain word present.
_INJURY_RECALL_PATTERN = re.compile(
    rf"\b(what('s| is) (going on|up|happening) with|how('s| is| are) my|status of|update on|"
    rf"remind me (about|what|how))\b[^?.!]{{0,30}}\b({_BODY_AREA_ALT})s?\b"
)

_RED_FLAG_PATTERN = re.compile(
    r"\b(chest pain|short of breath|shortness of breath|faint|fainted|dizzy|dizziness|numb|"
    r"numbness|tingling|tingles|tingly|gave out|tore|torn|popped|can'?t straighten|"
    r"can'?t put weight|sharp|stabbing|locked up|swollen|swelling)\b",
    re.IGNORECASE,
)

_MEDICAL_ASK_PATTERN = re.compile(
    r"\b(see|visit|go to)\s+(a\s+)?(doctor|physio|physiotherapist|pt|physical therapist|"
    r"chiro|chiropractor|ortho|orthopedist|specialist)\b",
    re.IGNORECASE,
)

_EXERCISES = (
    "bench press",
    "bench",
    "incline press",
    "incline chest press",
    "incline dumbbell press",
    "incline bench",
    "flat bench",
    "chest press",
    "cable chest press",
    "chest fly",
    "fly",
    "squat",
    "leg press",
    "leg extension",
    "leg extensions",
    "hamstring curl",
    "leg curl",
    "calf raise",
    "calf raises",
    "deadlift",
    "rdl",
    "romanian deadlift",
    "back extension",
    "row",
    "t-bar row",
    "t bar row",
    "mid-row",
    "mid row",
    "lat pulldown",
    "pulldown",
    "pull-up",
    "pull up",
    "pullup",
    "pull-ups",
    "pull ups",
    "chin-up",
    "chin up",
    "shoulder press",
    "overhead press",
    "ohp",
    "lateral raise",
    "lateral raises",
    "lat raise",
    "lat raises",
    "rear delt",
    "shrug",
    "shrugs",
    "curl",
    "curls",
    "bicep curl",
    "preacher curl",
    "incline curl",
    "hammer curl",
    "wrist curl",
    "pushdown",
    "pushdowns",
    "tricep extension",
    "kickback",
    "kickbacks",
    "skull crusher",
    "dip",
    "dips",
    "face pull",
)

_PROGRESS_TERMS = (
    "progress",
    "progressed",
    "progressing",
    "plateau",
    "plateaued",
    "stuck",
    "stall",
    "stalled",
    "stalling",
    "trend",
    "trending",
    "improv",
    "gone up",
    "going up",
    "moved",
    "hasn't moved",
    "hasnt moved",
    "not moving",
    "stronger",
    "weaker",
    "history",
    "last time",
    "last session",
    "my best",
    "my max",
    "my pr",
    "my numbers",
    "how much do i",
    "how much did i",
    "how much have i",
    "what did i",
    "what have i",
    "what weight",
    "what am i",
    "where am i",
    "am i getting",
    "lifting for",
    "been lifting",
    "been doing",
    "1rm",
    "one rep max",
    "average",
    "baseline",
    "topped out",
)

_LOCKED_TERMS = (
    "streak",
    "trend",
    "monthly",
    "progress report",
    "analytics",
    "stats",
    "statistics",
    "adherence",
    "completion rate",
    "consistency rate",
    "how consistent",
    "on average",
    "average",
    "over the last month",
    "over the past month",
    "this month",
    "last month",
    "past 30 days",
    "last 30 days",
    "past few weeks",
    "last few weeks",
    "past 4 weeks",
    "last 4 weeks",
    "past 6 weeks",
    "last 6 weeks",
    "how many workouts",
    "how many sessions",
    "how many days",
    "total volume",
    "overall progress",
    "overall trend",
    "summary of my",
    "how am i doing overall",
    "how have i been doing",
    "how's my consistency",
    "how is my consistency",
    "how's my adherence",
    "big picture",
)

_NUTRITION_TERMS = (
    "protein",
    "calorie",
    "calories",
    "carb",
    "carbs",
    "carbohydrate",
    "fat intake",
    "macro",
    "macros",
    "kcal",
    "deficit",
    "surplus",
    "creatine",
    "supplement",
    "water intake",
    "hydration",
    "meal",
    "eat",
    "eating",
    "diet",
)

_NUTRITION_TARGET_PATTERN = re.compile(
    r"\b(how (much|many)\b[^?.!]{0,40}\b(protein|calories|carbs|carbohydrates|fat|water)\b"
    r"|(protein|calorie|carb|macro|fat)s?\s+(target|goal|intake|split|number|numbers)"
    r"|what (should|are|is) my (protein|calorie|calories|carb|carbs|macro|macros|fat)"
    r"|(my|the) (protein|calorie|calories|carb|carbs|macro|macros|fat) (target|goal)s?"
    r"|how (much|many) (should|do) i (eat|be eating|consume))\b",
    re.IGNORECASE,
)

_SCHEDULE_PATTERN = re.compile(
    r"\b(what('s| is| should| do)?\s+(i|should i|do i|am i)?\s*(train|lift|workout|work out|do)"
    r"[^?.!]{0,30}\b(today|tonight|tomorrow|this (morning|afternoon|evening)|on (mon|tues|wednes|"
    r"thurs|fri|satur|sun)day)"
    r"|(today's|todays|tonight's|tonights|tomorrow's|tomorrows) "
    r"(workout|session|lift|training|plan|"
    r"cardio)"
    r"|what('s| is) (on|planned|scheduled) (for )?(today|tonight|tomorrow)"
    r"|(is|am i) (today|tomorrow|it) (a )?(rest|cardio|leg|arm|upper|lower|push|pull) ?(day)?"
    r"|(am i|do i|should i) (lifting|training|working out|doing cardio) (today|tonight|tomorrow)"
    r"|what day is (it|today|leg|arm|chest)"
    r"|(move|swap|shift|skip|push) (my )?(workout|session|leg day|arm day|training)"
    r"|(can|could) i (train|lift|workout|work out|do) [^?.!]{0,20}(today|tonight|tomorrow)"
    r"|what (do you )?(recommend|suggest) (for )?(today|tonight|tomorrow)"
    r"|(plan|program|schedule) (for )?(today|tonight|tomorrow|this week|the week))\b",
    re.IGNORECASE,
)

_RECENT_ACTIVITY_PATTERN = re.compile(
    r"\b(what (did|have) i (do|log|train|lift|eat|run|commit|promise|say|plan)\b"
    r"|(my |any |open )?commitments?\b"
    r"|when did i last\b"
    r"|(did|have) i (train|lift|log|do|run|eat)[^?.!]{0,25}(yesterday|today|this week|last week|"
    r"recently|lately)"
    r"|(my|the) last (workout|session|cardio|leg day|arm day|run|walk|lift)\b"
    r"|(yesterday's|yesterdays) (workout|session|cardio|lift|macros|sleep)"
    r"|how (did|was) (my )?(last|yesterday's|yesterdays) (workout|session|cardio|sleep|lift)"
    r"|(did i|have i) (hit|log|miss|skip)[^?.!]{0,25}(today|yesterday|this week)"
    r"|how (did|have) i sleep\b"
    r"|(my )?(sleep|macros|nutrition|cardio) (last night|yesterday|today|this week)"
    r"|(what|how much) (did|have) i (eat|log)[^?.!]{0,20}(today|yesterday|this week)"
    r"|this week('s)? (workouts|sessions|cardio|lifts|training))\b",
    re.IGNORECASE,
)

_KNOWLEDGE_PATTERN = re.compile(
    r"^\s*(why (do|does|is|are|should|would|can|can't|cant|don't|dont)\b"
    # "Will cardio hurt my lifting?" - a general question whose subject isn't the user.
    r"|(will|would|can|could|does|do|is|are)\s+(?!i\b|we\b|it\b|that\b|this\b|my\b|today\b|"
    r"tomorrow\b|tonight\b)[a-z][a-z\- ]{2,60}\?\s*$"
    r"|what('s| is| are) (the )?(difference|point|benefit|purpose|evidence|research|science)\b"
    r"|what does (the )?(research|evidence|science|literature) (say|show|suggest)"
    r"|is (it|there) (true|any evidence|evidence|research|proof|a benefit)\b"
    r"|does [^?.!]{1,60} (work|matter|help|make a difference|build|cause|improve|increase)\b"
    r"|do i (need|have) to\b"
    r"|how (does|do|important|much does|long should|many (sets|reps|times))\b"
    r"|explain\b|what('s| is) (a |an |the )?\w+( \w+){0,3} ?\?\s*$"
    r"|(is|are) [^?.!]{1,60} (good|bad|worth it|necessary|effective|better|worse|useful|overrated|"
    r"a myth|dangerous|safe)\b"
    r"|tell me about\b|thoughts on (the )?(science|evidence|research)\b)",
    re.IGNORECASE,
)

_JUDGMENT_PATTERN = re.compile(
    r"\b(should i|can i|could i|would it (be )?(better|help|make sense|be worth)|is it worth\b"
    r"|what do you think\b|do you think\b|recommend|suggest|thoughts on\b|(switch|change|replace|"
    r"swap|drop|add|remove|increase|decrease|reduce|bump|deload|cut|bulk|try)\b[^?.!]{0,60}\?"
    r"|worth (trying|adding|switching|changing)\b|or should\b|instead of\b|better to\b"
    r"|how should i\b|what('s| is) the best way\b|help me (decide|figure out|plan)\b)",
    re.IGNORECASE,
)

_CONVERSATIONAL_PATTERN = re.compile(
    r"^\s*(?:(?:thanks?(?: you| a lot| so much)?|thank you|thx|ty|ok(?:ay)?|k|got it|sounds good|"
    r"good to know|cool|nice|great|perfect|will do|noted|yep|yes|no|nah|hi|hello|hey|yo|"
    r"good (?:morning|night|evening|afternoon)|morning|lol|haha|👍|🙏|sure|alright|understood|"
    r"makes sense|cheers|bye|later|see ya|gm|gn|awesome|sweet|word|bet)[\s!.,?]*){1,4}$",
    re.IGNORECASE,
)

_ANAPHORA_PATTERN = re.compile(
    r"\b(that|this|it|those|these|same|again|the one|instead|also|too|what about|and the)\b",
    re.IGNORECASE,
)

_DURATION_PATTERN = re.compile(
    r"\b(\d+|a|one|two|three|four|five|six|eight|ten|twelve)\s+(day|week|month)s?\b"
)
_WORD_NUMBERS = {
    "a": 1,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "eight": 8,
    "ten": 10,
    "twelve": 12,
}
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def _find_terms(lowered: str, terms: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(term for term in terms if term in lowered)


_VERB_SUFFIX = r"(s|es|ing|ed|ting|ped|ping)?"


def _find_exercises(lowered: str) -> tuple[str, ...]:
    found = [
        name for name in _EXERCISES if re.search(rf"\b{re.escape(name)}{_VERB_SUFFIX}\b", lowered)
    ]
    # Keep the most specific names first (e.g. "incline chest press" before "press").
    found.sort(key=len, reverse=True)
    deduped: list[str] = []
    for name in found:
        if not any(name in longer for longer in deduped):
            deduped.append(name)
    return tuple(deduped)


def _find_body_areas(lowered: str) -> tuple[str, ...]:
    return tuple(area for area in _BODY_AREAS if re.search(rf"\b{re.escape(area)}s?\b", lowered))


def _window_days(lowered: str) -> int | None:
    match = _DURATION_PATTERN.search(lowered)
    if not match:
        return None
    raw, unit = match.group(1), match.group(2)
    count = int(raw) if raw.isdigit() else _WORD_NUMBERS.get(raw, 1)
    return count * {"day": 1, "week": 7, "month": 30}[unit]


def _mentions_self(lowered: str) -> bool:
    return bool(re.search(r"\b(i|i've|ive|i'm|im|my|me|mine)\b", lowered))


def classify_message(message: str, *, analytics_locked: bool = True) -> RoutingDecision:
    """Classify a free-text message into a route with an explicit source-need profile.

    Matchers run in a fixed priority order. Each returns a fully specified decision so
    the rest of the pipeline never has to infer what a category "means".
    """

    text = message.strip()
    lowered = text.lower()
    word_count = len(lowered.split())
    exercises = _find_exercises(lowered)
    body_areas = _find_body_areas(lowered)
    window = _window_days(lowered)
    anaphoric = bool(_ANAPHORA_PATTERN.search(lowered)) or word_count <= 5
    entities: dict[str, object] = {}
    if exercises:
        entities["exercises"] = list(exercises)
    if body_areas:
        entities["body_areas"] = list(body_areas)
    if window is not None:
        entities["window_days"] = window
    weekdays = [day for day in _WEEKDAYS if day in lowered]
    if weekdays:
        entities["weekdays"] = weekdays

    # 1. Safety: pain / injury / medical red flags. Deterministic, overrides everything.
    pain_terms = tuple(dict.fromkeys(m.group(0).lower() for m in _PAIN_PATTERN.finditer(lowered)))
    if (
        pain_terms
        and set(pain_terms) <= {"hurt", "hurts", "hurting"}
        and _HURT_ABSTRACT_PATTERN.search(lowered)
    ):
        pain_terms = ()
    joint_sore = bool(_JOINT_SORE_PATTERN.search(lowered))
    medical_ask = bool(_MEDICAL_ASK_PATTERN.search(lowered))
    injury_recall = bool(body_areas and not exercises and _INJURY_RECALL_PATTERN.search(lowered))
    if (
        (pain_terms and (body_areas or _mentions_self(lowered)))
        or joint_sore
        or medical_ask
        or injury_recall
    ):
        red_flags = tuple(
            dict.fromkeys(m.group(0).lower() for m in _RED_FLAG_PATTERN.finditer(lowered))
        )
        entities["red_flags"] = list(red_flags)
        entities["severity_hint"] = "high" if red_flags else "unknown"
        return RoutingDecision(
            category=RouteCategory.SAFETY,
            needs_conversation_context=True,
            needs_user_history=True,
            needs_calculations=False,
            research_allowed=False,
            stable_knowledge=True,
            tools=(
                TOOL_ACTIVE_CONSTRAINTS,
                TOOL_EXERCISE_HISTORY,
                TOOL_RECENT_EVENTS,
                TOOL_SEARCH_KB,
                TOOL_LOG_INJURY,
                TOOL_REMEMBER_FACT,
            ),
            matched_terms=pain_terms
            or (("sore",) if joint_sore else ("recall",) if injury_recall else ("medical",)),
            entities=entities,
            reason="pain/injury language detected; conservative handling, no external research",
        )

    # 2. Named-exercise history (single-lift, un-aggregated) - allowed even under the lock.
    progress_terms = _find_terms(lowered, _PROGRESS_TERMS)
    if exercises and (progress_terms or _RECENT_ACTIVITY_PATTERN.search(lowered)):
        return RoutingDecision(
            category=RouteCategory.EXERCISE_HISTORY,
            needs_conversation_context=anaphoric,
            needs_user_history=True,
            needs_calculations=True,
            research_allowed=False,
            stable_knowledge=True,
            tools=(
                TOOL_EXERCISE_HISTORY,
                TOOL_ACTIVE_CONSTRAINTS,
                TOOL_RECENT_EVENTS,
                TOOL_SEARCH_KB,
            ),
            matched_terms=progress_terms,
            entities=entities,
            reason="named exercise plus progress/history language",
        )

    # 3. Cumulative analytics lock (structural: no tool below computes these anyway).
    locked_terms = _find_terms(lowered, _LOCKED_TERMS)
    if analytics_locked and locked_terms and _mentions_self(lowered):
        return RoutingDecision(
            category=RouteCategory.LOCKED_ANALYTICS,
            needs_conversation_context=False,
            needs_user_history=False,
            needs_calculations=False,
            research_allowed=False,
            stable_knowledge=False,
            tools=(),
            matched_terms=locked_terms,
            entities=entities,
            reason="cumulative-analytics request; redirected to the progress-review cadence",
        )

    # 4. Nutrition targets: a config lookup, never a history query.
    if _NUTRITION_TARGET_PATTERN.search(lowered):
        return RoutingDecision(
            category=RouteCategory.NUTRITION_TARGETS,
            needs_conversation_context=False,
            needs_user_history=False,
            needs_calculations=False,
            research_allowed=False,
            stable_knowledge=True,
            tools=(TOOL_NUTRITION_TARGETS, TOOL_SEARCH_KB),
            matched_terms=_find_terms(lowered, _NUTRITION_TERMS),
            entities=entities,
            reason="asks for a nutrition target; answer from configured goals",
        )

    # 5. Schedule / today's plan: weekday -> split lookup plus constraints and overrides.
    if _SCHEDULE_PATTERN.search(lowered):
        return RoutingDecision(
            category=RouteCategory.SCHEDULE,
            needs_conversation_context=anaphoric,
            needs_user_history=True,
            needs_calculations=False,
            research_allowed=False,
            stable_knowledge=False,
            tools=(TOOL_TODAYS_PLAN, TOOL_ACTIVE_CONSTRAINTS, TOOL_RECENT_EVENTS),
            matched_terms=("schedule",),
            entities=entities,
            reason="asks what to train / today's plan",
        )

    # 6. Recent raw activity ("what did I do", "last session"): bounded event lookups.
    if _RECENT_ACTIVITY_PATTERN.search(lowered):
        tools: tuple[str, ...] = (TOOL_RECENT_EVENTS, TOOL_ACTIVE_CONSTRAINTS)
        if exercises:
            tools = (TOOL_RECENT_EVENTS, TOOL_EXERCISE_HISTORY, TOOL_ACTIVE_CONSTRAINTS)
        return RoutingDecision(
            category=RouteCategory.RECENT_ACTIVITY,
            needs_conversation_context=anaphoric,
            needs_user_history=True,
            needs_calculations=False,
            research_allowed=False,
            stable_knowledge=False,
            tools=tools,
            matched_terms=("recent_activity",),
            entities=entities,
            reason="asks about recently logged events",
        )

    # 7. Conversational filler: no tools, no history.
    if _CONVERSATIONAL_PATTERN.match(lowered):
        return RoutingDecision(
            category=RouteCategory.CONVERSATIONAL,
            needs_conversation_context=True,
            needs_user_history=False,
            needs_calculations=False,
            research_allowed=False,
            stable_knowledge=False,
            tools=(),
            matched_terms=("conversational",),
            entities=entities,
            reason="acknowledgement/greeting",
        )

    # 8. Short anaphoric follow-ups ("is that better?") depend on the conversation window.
    if (
        word_count <= 6
        and re.search(r"\b(that|it|this|those|these)\b", lowered)
        and not (exercises or body_areas)
    ):
        return RoutingDecision(
            category=RouteCategory.JUDGMENT,
            needs_conversation_context=True,
            needs_user_history=True,
            needs_calculations=False,
            research_allowed=True,
            stable_knowledge=True,
            tools=ALL_CHAT_TOOLS,
            matched_terms=("follow_up",),
            entities=entities,
            reason="short anaphoric follow-up; resolve against recent conversation",
        )

    # 9. Explicit general-knowledge phrasing ("why do...", "how many sets...") beats
    #    the looser "should I" judgment cue.
    knowledge_hit = bool(_KNOWLEDGE_PATTERN.search(lowered))
    if knowledge_hit and not progress_terms:
        return RoutingDecision(
            category=RouteCategory.KNOWLEDGE,
            needs_conversation_context=anaphoric,
            needs_user_history=False,
            needs_calculations=False,
            research_allowed=True,
            stable_knowledge=True,
            tools=(TOOL_SEARCH_KB, TOOL_EXTERNAL_RESEARCH, TOOL_ACTIVE_CONSTRAINTS),
            matched_terms=("knowledge",),
            entities=entities,
            reason="general fitness question without personal-history dependence",
        )

    # 10. Judgment calls ("should I ...") get the full chat-safe tool set.
    if _JUDGMENT_PATTERN.search(lowered):
        return RoutingDecision(
            category=RouteCategory.JUDGMENT,
            needs_conversation_context=True,
            needs_user_history=True,
            needs_calculations=True,
            research_allowed=True,
            stable_knowledge=True,
            tools=ALL_CHAT_TOOLS,
            matched_terms=("judgment",),
            entities=entities,
            reason="coaching judgment/synthesis question; LLM selects evidence via tools",
        )

    # 11. Impersonal questions default to knowledge: stable knowledge first.
    if not _mentions_self(lowered):
        return RoutingDecision(
            category=RouteCategory.KNOWLEDGE,
            needs_conversation_context=anaphoric,
            needs_user_history=False,
            needs_calculations=False,
            research_allowed=True,
            stable_knowledge=True,
            tools=(TOOL_SEARCH_KB, TOOL_EXTERNAL_RESEARCH, TOOL_ACTIVE_CONSTRAINTS),
            matched_terms=("knowledge",),
            entities=entities,
            reason="general fitness question without personal-history dependence",
        )

    # 12. Fallthrough: personal but unclassified -> judgment with full tools.
    return RoutingDecision(
        category=RouteCategory.JUDGMENT,
        needs_conversation_context=True,
        needs_user_history=True,
        needs_calculations=True,
        research_allowed=True,
        stable_knowledge=True,
        tools=ALL_CHAT_TOOLS,
        matched_terms=(),
        entities=entities,
        reason="unclassified personal question; LLM selects evidence via tools",
    )
