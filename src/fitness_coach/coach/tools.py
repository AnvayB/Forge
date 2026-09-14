"""Deterministic tools the chat model may call.

Every tool returns JSON-serializable data split into `raw` (events exactly as logged)
and `derived` (numbers computed by code - Epley 1RM, stall streaks, baseline verdicts).
Nothing here narrates; interpretation is the model's job and happens in the reply.

The registry is the analytics lock: no function that computes streaks, adherence, or
multi-week averages is defined here, so the chat model structurally cannot reach them.
Those live only in `analytics/reports.py` and feed the scheduled progress review.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fitness_coach.analytics.strength import (
    _one_rep_max_estimate,
    best_set_among,
    judge_against_baseline,
)
from fitness_coach.config.settings import CoachSettings
from fitness_coach.database import models
from fitness_coach.database.repositories import (
    CardioEventRepository,
    CommitmentEventRepository,
    ConversationMemoryRepository,
    ExerciseBaselineRepository,
    InjuryHistoryRepository,
    MeasurementEventRepository,
    NutritionEventRepository,
    PlanOverrideRepository,
    SleepEventRepository,
    WorkoutEventRepository,
)
from fitness_coach.research.engine import ResearchEngine, ResearchResult
from fitness_coach.routing.classifier import (
    TOOL_ACTIVE_CONSTRAINTS,
    TOOL_EXERCISE_HISTORY,
    TOOL_EXTERNAL_RESEARCH,
    TOOL_LOG_INJURY,
    TOOL_NUTRITION_TARGETS,
    TOOL_RECENT_EVENTS,
    TOOL_REMEMBER_FACT,
    TOOL_SEARCH_KB,
    TOOL_TODAYS_PLAN,
)

logger = logging.getLogger(__name__)

_MAX_EVENTS = 20
_MAX_HISTORY_DAYS = 120
_DEFAULT_HISTORY_DAYS = 42
_STALL_SESSIONS = 3

_EXERCISE_ALIASES: dict[str, tuple[str, ...]] = {
    "ohp": ("overhead press", "shoulder press"),
    "bench": ("bench press", "bench"),
    "rdl": ("romanian deadlift", "rdl"),
    "pulldown": ("lat pulldown", "pulldown"),
    "lat raise": ("lateral raise",),
    "lat raises": ("lateral raise",),
}


def _normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", name.lower()).strip()


def _name_tokens(name: str) -> set[str]:
    return set(_normalize(name).split())


def _exercise_matches(query: str, candidate: str) -> bool:
    q = _normalize(query)
    c = _normalize(candidate)
    if not q or not c:
        return False
    if q in c or c in q:
        return True
    q_tokens = _name_tokens(q)
    c_tokens = _name_tokens(c)
    if q_tokens and q_tokens <= c_tokens:
        return True
    for alias in _EXERCISE_ALIASES.get(q, ()):
        if alias in c:
            return True
    return False


def _iso(value: datetime | date | None) -> str | None:
    return value.isoformat() if value else None


TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    TOOL_RECENT_EVENTS: {
        "type": "function",
        "name": TOOL_RECENT_EVENTS,
        "description": (
            "Raw recently logged events for a bounded window (max 14 days, max 20 events). "
            "Use for 'what did I do / log' questions. Returns records, never trends."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "event_type": {
                    "type": "string",
                    "enum": ["workout", "cardio", "nutrition", "sleep", "measurement", "all"],
                },
                "days": {"type": "integer", "minimum": 1, "maximum": 14},
            },
            "required": ["event_type"],
        },
        "strict": False,
    },
    TOOL_EXERCISE_HISTORY: {
        "type": "function",
        "name": TOOL_EXERCISE_HISTORY,
        "description": (
            "Per-session sets for one named exercise plus code-computed facts (best set, "
            "estimated 1RM per session, consecutive non-improving sessions, baseline verdict). "
            "Call this before saying anything about how a lift has been going."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "exercise_name": {"type": "string"},
                "days": {"type": "integer", "minimum": 7, "maximum": _MAX_HISTORY_DAYS},
            },
            "required": ["exercise_name"],
        },
        "strict": False,
    },
    TOOL_ACTIVE_CONSTRAINTS: {
        "type": "function",
        "name": TOOL_ACTIVE_CONSTRAINTS,
        "description": (
            "Active injuries, exercises to avoid, active schedule overrides, open commitments, "
            "and remembered facts. Small and safety-relevant; check before recommending exercises."
        ),
        "parameters": {"type": "object", "properties": {}},
        "strict": False,
    },
    TOOL_TODAYS_PLAN: {
        "type": "function",
        "name": TOOL_TODAYS_PLAN,
        "description": (
            "Scheduled split and exercise list for a date (default today) from the training "
            "preferences, whether it is a cardio day, active overrides, and the last logged "
            "workout."
        ),
        "parameters": {
            "type": "object",
            "properties": {"date": {"type": "string", "description": "ISO date, optional"}},
        },
        "strict": False,
    },
    TOOL_NUTRITION_TARGETS: {
        "type": "function",
        "name": TOOL_NUTRITION_TARGETS,
        "description": "Configured daily calorie/macro targets and today's logged totals if any.",
        "parameters": {"type": "object", "properties": {}},
        "strict": False,
    },
    TOOL_SEARCH_KB: {
        "type": "function",
        "name": TOOL_SEARCH_KB,
        "description": (
            "Search the curated knowledge base. Returns matching entries with exact Source URLs "
            "and an evidence tier per source. Cite these URLs verbatim."
        ),
        "parameters": {
            "type": "object",
            "properties": {"topic": {"type": "string"}},
            "required": ["topic"],
        },
        "strict": False,
    },
    TOOL_EXTERNAL_RESEARCH: {
        "type": "function",
        "name": TOOL_EXTERNAL_RESEARCH,
        "description": (
            "Escalate to bounded external research ONLY when search_knowledge_base returned "
            "nothing relevant and the topic is fast-moving or unfamiliar. Any source returned "
            "must be cited with the exact label provided. One call per reply."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {"type": "string"},
                "reason": {
                    "type": "string",
                    "description": "Why the knowledge base is insufficient",
                },
            },
            "required": ["topic"],
        },
        "strict": False,
    },
    TOOL_REMEMBER_FACT: {
        "type": "function",
        "name": TOOL_REMEMBER_FACT,
        "description": (
            "Durably remember a stable fact the user stated (preference, constraint, goal). "
            "Not for one-off remarks."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "snake_case key, e.g. preferred_training_time",
                },
                "value": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["key", "value"],
        },
        "strict": False,
    },
    TOOL_LOG_INJURY: {
        "type": "function",
        "name": TOOL_LOG_INJURY,
        "description": (
            "Record or update a pain/injury report so future workouts account for it. "
            "Severity 1 (mild) to 5 (severe)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "body_area": {"type": "string"},
                "description": {"type": "string"},
                "severity": {"type": "integer", "minimum": 1, "maximum": 5},
            },
            "required": ["body_area", "description"],
        },
        "strict": False,
    },
}


class CoachToolkit:
    """Request-scoped tool implementations bound to one user's repositories."""

    def __init__(
        self,
        *,
        settings: CoachSettings,
        config_dir: Path,
        workouts: WorkoutEventRepository,
        cardio: CardioEventRepository,
        nutrition: NutritionEventRepository,
        sleep: SleepEventRepository,
        measurements: MeasurementEventRepository,
        commitments: CommitmentEventRepository,
        plan_overrides: PlanOverrideRepository,
        exercise_baselines: ExerciseBaselineRepository,
        memory: ConversationMemoryRepository,
        injuries: InjuryHistoryRepository,
        research: ResearchEngine,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.settings = settings
        self.config_dir = config_dir
        self.workouts = workouts
        self.cardio = cardio
        self.nutrition = nutrition
        self.sleep = sleep
        self.measurements = measurements
        self.commitments = commitments
        self.plan_overrides = plan_overrides
        self.exercise_baselines = exercise_baselines
        self.memory = memory
        self.injuries = injuries
        self.research = research
        self._now = now or (lambda: datetime.now(UTC))
        self.user_id: str | None = None
        self.research_allowed = False
        self.risk = False
        self.calls: list[dict[str, Any]] = []
        self.external_urls: set[str] = set()
        self.research_results: list[ResearchResult] = []
        self._external_calls = 0

    # --- request binding --------------------------------------------------------------

    def bind(self, user_id: str, *, research_allowed: bool, risk: bool) -> None:
        self.user_id = user_id
        self.research_allowed = research_allowed
        self.risk = risk
        self.calls = []
        self.external_urls = set()
        self.research_results = []
        self._external_calls = 0

    def schemas_for(self, names: tuple[str, ...] | list[str]) -> list[dict[str, Any]]:
        return [TOOL_SCHEMAS[name] for name in names if name in TOOL_SCHEMAS]

    def dispatch(self, name: str, arguments: str | dict[str, Any] | None) -> str:
        """Execute a tool by name with JSON arguments; always returns a JSON string."""

        if self.user_id is None:
            raise RuntimeError("CoachToolkit.bind must be called before dispatch")
        args: dict[str, Any]
        if isinstance(arguments, dict):
            args = arguments
        else:
            try:
                args = json.loads(arguments) if arguments else {}
            except json.JSONDecodeError:
                args = {}
        handler = self._handlers().get(name)
        record: dict[str, Any] = {"name": name, "arguments": args}
        if handler is None:
            record["error"] = "unknown tool"
            self.calls.append(record)
            return json.dumps({"error": f"Unknown tool: {name}"})
        try:
            result = handler(**args)
            record["ok"] = True
        except TypeError as error:
            result = {"error": f"Bad arguments for {name}: {error}"}
            record["error"] = str(error)
        except Exception as error:  # noqa: BLE001 - tool failures become model-visible errors
            logger.exception("tool_failed name=%s", name)
            result = {"error": f"{name} failed: {error}"}
            record["error"] = str(error)
        self.calls.append(record)
        return json.dumps(result, default=str)

    def _handlers(self) -> dict[str, Callable[..., dict[str, Any]]]:
        return {
            TOOL_RECENT_EVENTS: self.get_recent_events,
            TOOL_EXERCISE_HISTORY: self.get_exercise_history,
            TOOL_ACTIVE_CONSTRAINTS: self.get_active_constraints,
            TOOL_TODAYS_PLAN: self.get_todays_plan,
            TOOL_NUTRITION_TARGETS: self.get_nutrition_targets,
            TOOL_SEARCH_KB: self.search_knowledge_base,
            TOOL_EXTERNAL_RESEARCH: self.flag_for_external_research,
            TOOL_REMEMBER_FACT: self.remember_fact,
            TOOL_LOG_INJURY: self.log_injury,
        }

    # --- helpers ----------------------------------------------------------------------

    @property
    def _uid(self) -> str:
        assert self.user_id is not None
        return self.user_id

    def _today(self) -> date:
        return self._now().astimezone(ZoneInfo(self.settings.timezone)).date()

    def _window(self, days: int) -> tuple[datetime, datetime]:
        end = self._now()
        return end - timedelta(days=days), end

    # --- read tools -------------------------------------------------------------------

    def get_recent_events(self, event_type: str = "all", days: int = 7) -> dict[str, Any]:
        days = max(1, min(int(days), 14))
        start, end = self._window(days)
        raw: dict[str, list[dict[str, Any]]] = {}
        if event_type in ("workout", "all"):
            events = sorted(
                self.workouts.between(self._uid, start, end),
                key=lambda e: e.occurred_at,
                reverse=True,
            )[:_MAX_EVENTS]
            raw["workouts"] = [
                {
                    "date": _iso(e.occurred_at),
                    "workout_type": e.workout_type,
                    "duration_minutes": e.duration_minutes,
                    "exercises": [
                        {"name": x.get("name"), "sets": x.get("sets")} for x in (e.exercises or [])
                    ],
                }
                for e in events
            ]
        if event_type in ("cardio", "all"):
            events = sorted(
                self.cardio.between(self._uid, start, end),
                key=lambda e: e.occurred_at,
                reverse=True,
            )[:_MAX_EVENTS]
            raw["cardio"] = [
                {
                    "date": _iso(e.occurred_at),
                    "modality": e.modality,
                    "duration_minutes": e.duration_minutes,
                    "distance_miles": e.distance_miles,
                    "average_heart_rate": e.average_heart_rate,
                }
                for e in events
            ]
        if event_type in ("nutrition", "all"):
            events = sorted(
                self.nutrition.between(self._uid, start, end),
                key=lambda e: e.logged_for,
                reverse=True,
            )[:_MAX_EVENTS]
            raw["nutrition"] = [
                {
                    "date": _iso(e.logged_for),
                    "calories": e.calories,
                    "protein_g": e.protein_g,
                    "carbs_g": e.carbs_g,
                    "fat_g": e.fat_g,
                }
                for e in events
            ]
        if event_type in ("sleep", "all"):
            events = sorted(
                self.sleep.between(self._uid, start, end), key=lambda e: e.logged_for, reverse=True
            )[:_MAX_EVENTS]
            raw["sleep"] = [
                {
                    "date": _iso(e.logged_for),
                    "time_asleep_minutes": e.time_asleep_minutes,
                    "regularity_percent": e.regularity_percent,
                    "wake_up_mood": e.wake_up_mood,
                }
                for e in events
            ]
        if event_type in ("measurement", "all"):
            events = sorted(
                self.measurements.between(self._uid, start, end),
                key=lambda e: e.measured_at,
                reverse=True,
            )[:_MAX_EVENTS]
            raw["measurements"] = [
                {
                    "date": _iso(e.measured_at),
                    "body_weight_lb": e.body_weight_lb,
                    "waist_inches": e.waist_inches,
                }
                for e in events
            ]
        return {
            "window_days": days,
            "raw": raw,
            "note": "Raw logged records only. No streaks, averages, or trends are computed here.",
        }

    def get_exercise_history(
        self, exercise_name: str, days: int = _DEFAULT_HISTORY_DAYS
    ) -> dict[str, Any]:
        days = max(7, min(int(days), _MAX_HISTORY_DAYS))
        start, end = self._window(days)
        workouts = sorted(self.workouts.between(self._uid, start, end), key=lambda e: e.occurred_at)

        matched: dict[str, list[dict[str, Any]]] = {}
        for workout in workouts:
            for exercise in workout.exercises or []:
                name = str(exercise.get("name", "")).strip()
                if not name or not _exercise_matches(exercise_name, name):
                    continue
                sets = [
                    {"weight": s.get("weight"), "reps": s.get("reps")}
                    for s in (exercise.get("sets") or [])
                    if isinstance(s, dict)
                ]
                best = best_set_among([exercise]).get(name.lower())
                matched.setdefault(name, []).append(
                    {
                        "date": _iso(workout.occurred_at),
                        "sets": sets,
                        "top_set": {"weight": best[0], "reps": best[1]} if best else None,
                        "estimated_1rm": round(_one_rep_max_estimate(*best), 1) if best else None,
                    }
                )

        exercises_out = []
        for name, sessions in matched.items():
            running_best = 0.0
            non_improving = 0
            last_pr_date: str | None = None
            for session in sessions:
                est = session["estimated_1rm"] or 0.0
                if est > running_best:
                    running_best = est
                    non_improving = 0
                    last_pr_date = session["date"]
                else:
                    non_improving += 1
            first_top = next((s["top_set"] for s in sessions if s["top_set"]), None)
            last_top = next((s["top_set"] for s in reversed(sessions) if s["top_set"]), None)
            baseline = self.exercise_baselines.get_for_exercise(self._uid, name)
            baseline_out: dict[str, Any] | None = None
            if baseline is not None and last_top and last_top["weight"] is not None:
                verdict, near_max = judge_against_baseline(
                    logged_weight=float(last_top["weight"]),
                    baseline_weight=baseline.baseline_weight,
                    max_weight=baseline.max_weight,
                )
                baseline_out = {
                    "baseline_weight": baseline.baseline_weight,
                    "max_weight": baseline.max_weight,
                    "last_session_verdict": verdict,
                    "near_max": near_max,
                    "consecutive_sessions_at_tracked_weight": (
                        baseline.consecutive_sessions_at_tracked_weight
                    ),
                }
            exercises_out.append(
                {
                    "exercise": name,
                    "raw": {"sessions": sessions},
                    "derived": {
                        "sessions": len(sessions),
                        "best_estimated_1rm": round(running_best, 1) if running_best else None,
                        "first_top_set": first_top,
                        "last_top_set": last_top,
                        "consecutive_non_improving_sessions": non_improving,
                        "stalled": non_improving >= _STALL_SESSIONS,
                        "last_improvement_date": last_pr_date,
                        "baseline": baseline_out,
                    },
                }
            )

        return {
            "query": exercise_name,
            "window_days": days,
            "exercises": exercises_out,
            "note": (
                "raw = sets exactly as logged; derived = deterministic (Epley 1RM, stall = "
                f"{_STALL_SESSIONS}+ consecutive sessions without a new best). No interpretation."
                if exercises_out
                else "No sessions matched this exercise in the window."
            ),
        }

    def get_active_constraints(self) -> dict[str, Any]:
        today = self._today()
        injuries = self.injuries.active_for_user(self._uid)
        overrides = self.plan_overrides.active_for_user(self._uid, today)
        commitments = self.commitments.open_for_user(self._uid)
        facts = self.memory.all_for_user(self._uid)
        return {
            "today": {"date": today.isoformat(), "weekday": today.strftime("%A")},
            "active_injuries": [
                {
                    "body_area": i.body_area,
                    "description": i.description,
                    "status": i.status.value,
                    "severity": i.severity,
                    "last_noted_at": _iso(i.last_noted_at),
                }
                for i in injuries
            ],
            "exercises_to_avoid": _parse_avoid_list(self._training_preferences()),
            "active_plan_overrides": [
                {
                    "description": o.description,
                    "starts_on": o.starts_on.isoformat(),
                    "expires_on": o.expires_on.isoformat(),
                }
                for o in overrides
            ],
            "open_commitments": [
                {"description": c.description, "due_at": _iso(c.due_at)} for c in commitments
            ],
            "remembered_facts": {f.key: f.value for f in facts},
        }

    def get_todays_plan(self, date: str | None = None) -> dict[str, Any]:  # noqa: A002
        target = date_from_iso(date) if date else self._today()
        weekday = target.strftime("%A")
        prefs = self._training_preferences()
        split = _parse_split(prefs)
        focus = split.get(weekday, "Rest")
        exercises = _parse_day_exercises(prefs, weekday)
        overrides = self.plan_overrides.active_for_user(self._uid, target)
        last = self.workouts.recent(self._uid, limit=1)
        last_workout = (
            {"date": _iso(last[0].occurred_at), "workout_type": last[0].workout_type}
            if last
            else None
        )
        return {
            "date": target.isoformat(),
            "weekday": weekday,
            "scheduled_focus": focus,
            "is_rest_day": focus.lower() == "rest",
            "is_cardio_day": weekday in set(self.settings.cardio_days),
            "cardio_goal_minutes": self.settings.default_cardio_goal_minutes,
            "exercises": exercises,
            "active_plan_overrides": [
                {"description": o.description, "expires_on": o.expires_on.isoformat()}
                for o in overrides
            ],
            "last_logged_workout": last_workout,
        }

    def get_nutrition_targets(self) -> dict[str, Any]:
        today = self._today()
        start = datetime.combine(today, datetime.min.time(), tzinfo=UTC) - timedelta(days=1)
        end = datetime.combine(today, datetime.max.time(), tzinfo=UTC) + timedelta(days=1)
        todays = [
            e for e in self.nutrition.between(self._uid, start, end) if e.logged_for.date() == today
        ]
        logged = todays[0] if todays else None
        return {
            "targets": {
                "calories": self.settings.calorie_goal,
                "protein_g": self.settings.protein_goal_g,
                "protein_adherence_threshold_g": self.settings.protein_adherence_threshold_g,
                "carbs_g": self.settings.carbs_goal_g,
                "fat_g": self.settings.fat_goal_g,
            },
            "today_logged": (
                {
                    "calories": logged.calories,
                    "protein_g": logged.protein_g,
                    "carbs_g": logged.carbs_g,
                    "fat_g": logged.fat_g,
                }
                if logged
                else None
            ),
            "note": "Targets come from coach_settings.yaml. No averages or trends are computed.",
        }

    def search_knowledge_base(self, topic: str) -> dict[str, Any]:
        entries = self.research.knowledge_base.search(topic)
        return {
            "topic": topic,
            "entries": [entry.as_dict() for entry in entries],
            "note": (
                "Cite Source URLs exactly as given, unlabeled."
                if entries
                else "No curated entry covers this topic."
            ),
        }

    def flag_for_external_research(self, topic: str, reason: str = "") -> dict[str, Any]:
        if self._external_calls >= 1:
            return {"error": "External research budget for this reply is exhausted (1 call)."}
        self._external_calls += 1
        result = self.research.investigate(
            topic, risk=self.risk, allow_external=self.research_allowed
        )
        self.research_results.append(result)
        self.external_urls |= result.external_urls
        payload = result.as_dict()
        payload["reason_given"] = reason
        return payload

    # --- write tools ------------------------------------------------------------------

    def remember_fact(self, key: str, value: str, confidence: float = 0.9) -> dict[str, Any]:
        key = re.sub(r"[^a-z0-9_]+", "_", key.strip().lower()).strip("_")[:120] or "fact"
        self.memory.upsert_fact(
            user_id=self._uid,
            key=key,
            value={"value": value, "remembered_at": self._now().isoformat()},
            confidence=max(0.0, min(1.0, float(confidence))),
            source="conversation",
        )
        return {"remembered": {"key": key, "value": value}}

    def log_injury(
        self, body_area: str, description: str, severity: int | None = None
    ) -> dict[str, Any]:
        return self.record_pain_report(body_area, description, severity=severity)

    def record_pain_report(
        self, body_area: str, description: str, *, severity: int | None = None
    ) -> dict[str, Any]:
        """Create or refresh an injury record. Deterministic; also used by the safety route."""

        now = self._now()
        area = body_area.strip().lower()
        existing = next(
            (i for i in self.injuries.active_for_user(self._uid) if i.body_area.lower() == area),
            None,
        )
        if existing is None:
            record = self.injuries.add(
                models.InjuryHistory(
                    user_id=self._uid,
                    body_area=area,
                    description=description.strip()[:500],
                    status=models.InjuryStatus.MONITORING,
                    severity=severity,
                    first_noted_at=now,
                    last_noted_at=now,
                )
            )
            return {
                "injury": {"body_area": area, "status": record.status.value, "reports": 1},
                "note": "New pain report recorded for monitoring.",
            }
        existing.last_noted_at = now
        if severity is not None:
            existing.severity = severity
        prior_reports = (
            int(re.search(r"\[reports:(\d+)\]", existing.description or "").group(1))
            if (re.search(r"\[reports:(\d+)\]", existing.description or ""))
            else 1
        )
        base = re.sub(r"\s*\[reports:\d+\]\s*$", "", existing.description or "").strip()
        existing.description = f"{base or description.strip()[:400]} [reports:{prior_reports + 1}]"
        self.injuries.session.flush()
        return {
            "injury": {
                "body_area": area,
                "status": existing.status.value,
                "reports": prior_reports + 1,
                "first_noted_at": _iso(existing.first_noted_at),
            },
            "note": "Recurring report on an area already being monitored.",
        }

    # --- config parsing ---------------------------------------------------------------

    def _training_preferences(self) -> str:
        path = self.config_dir / "training_preferences.md"
        return path.read_text(encoding="utf-8") if path.exists() else ""


def date_from_iso(value: str) -> date:
    return datetime.fromisoformat(value).date()


_SPLIT_LINE = re.compile(r"^- (Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday):\s*(.+)$")


def _parse_split(text: str) -> dict[str, str]:
    split: dict[str, str] = {}
    section = text.split("# Preferred Split", 1)[-1].split("\n---", 1)[0]
    for line in section.splitlines():
        match = _SPLIT_LINE.match(line.strip())
        if match:
            split[match.group(1)] = match.group(2).strip()
    return split


def _parse_day_exercises(text: str, weekday: str) -> list[str]:
    pattern = re.compile(rf"^## {weekday}\b[^\n]*\n", re.MULTILINE)
    match = pattern.search(text)
    if not match:
        return []
    body = text[match.end() :]
    body = re.split(r"^## |^---", body, maxsplit=1, flags=re.MULTILINE)[0]
    exercises: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            item = re.sub(r"\*\*", "", stripped[2:]).strip()
            exercises.append(item)
    return exercises


def _parse_avoid_list(text: str) -> list[str]:
    section = text.split("# Exercises to Avoid", 1)
    if len(section) < 2:
        return []
    body = section[1].split("\n---", 1)[0]
    return [line.strip()[2:].strip() for line in body.splitlines() if line.strip().startswith("- ")]
