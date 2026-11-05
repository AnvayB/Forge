"""Core coaching orchestration."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from fitness_coach.analytics.progression import Action, suggest_next
from fitness_coach.analytics.reports import build_progress_metrics
from fitness_coach.analytics.sets import (
    SetType,
    classify_sets,
    normalize_exercises,
    parse_number,
    parse_set_numbers,
    parse_set_type,
)
from fitness_coach.analytics.strength import (
    PROMOTION_STREAK,
    PersonalRecord,
    best_set_among,
    find_new_personal_records,
    judge_against_baseline,
    next_tracked_weight,
    top_working_set,
)
from fitness_coach.coach.conversation import ConversationWindow
from fitness_coach.coach.openai_client import CoachOpenAIClient
from fitness_coach.coach.tools import CoachToolkit
from fitness_coach.config.prompt_builder import PromptBuilder
from fitness_coach.config.settings import CoachSettings
from fitness_coach.database import models
from fitness_coach.database.repositories import (
    CardioEventRepository,
    CommitmentEventRepository,
    ConversationMemoryRepository,
    ExerciseBaselineRepository,
    MeasurementEventRepository,
    NutritionEventRepository,
    PlanOverrideRepository,
    ProgressReviewRepository,
    SleepEventRepository,
    UserRepository,
    WorkoutEventRepository,
    WorkoutPlanRepository,
)
from fitness_coach.database.schemas import (
    CardioLog,
    CoachResponse,
    CommitmentCreate,
    NutritionLog,
    PlanOverrideCreate,
    SleepLog,
    WorkoutLog,
)
from fitness_coach.research.engine import EXTERNAL_LABEL
from fitness_coach.routing.classifier import RouteCategory, RoutingDecision, classify_message
from fitness_coach.routing.guidance import guidance_for
from fitness_coach.vision.processor import ImageKind, VisionExtraction

logger = logging.getLogger(__name__)

_RESPONSE_URL_PATTERN = re.compile(r"https?://\S+")
_EXTERNAL_LABEL_LOOKBACK = 260
"""How far before a cited URL the external label may appear and still count as labeling it."""


class AnalyticsLockedError(PermissionError):
    """Raised when a request would reveal locked cumulative analytics."""


_PR_HISTORY_LIMIT = 2000
"""How many past workouts to scan for a personal-record comparison.

PR detection is a single-event fact (did this set beat every prior one), not a
cumulative statistic, so it's exempt from the analytics lock - see the Analytics
Philosophy in coach_principles.md and "acknowledge meaningful effort" under
Accountability Philosophy.
"""


REVIEW_WORD_LIMIT = 300

_REVIEW_INSTRUCTIONS = (
    "Write the 30-day progress review from these deterministic metrics (JSON). Do not "
    "calculate new analytics; every number you cite must appear in the metrics.\n"
    "Format: short plain-text sections in this order, skipping any section with nothing "
    "meaningful to say: Period, Adherence, Workouts, Cardio, Nutrition, Measurements, "
    "Sleep, Bottom line.\n"
    "Rules:\n"
    "- Lead with the 1-3 takeaways the data best supports. Do not comment on every metric "
    "or every exercise.\n"
    "- Workouts: name the exercises in `highlights` (real progress or stalls). Volume counts "
    "working sets only and says how much was done, not how well.\n"
    "- Say plainly when something is working and needs no change. Suggest an adjustment only "
    "when the data supports it; never invent a problem.\n"
    "- Keep logging coverage separate from results: `coverage` and `logging_rate` say how "
    "much was logged; averages_logged_days and the goal rates say what happened.\n"
    "- No per-section 'Interpretation' lines, no generic encouragement, no explaining what "
    "a metric means, no hedging about how a streak is counted.\n"
    "- Never encourage training to failure or chasing maximal weights.\n"
    "- End with a one-sentence bottom line and one concrete next target.\n"
    f"- Stay under {REVIEW_WORD_LIMIT} words.\n"
    "Metrics:"
)


class CoachService:
    """Coordinates accountability flows while persisting structured events only."""

    def __init__(
        self,
        *,
        settings: CoachSettings,
        prompt_builder: PromptBuilder,
        openai_client: CoachOpenAIClient,
        users: UserRepository,
        workouts: WorkoutEventRepository,
        cardio: CardioEventRepository,
        nutrition: NutritionEventRepository,
        sleep: SleepEventRepository,
        measurements: MeasurementEventRepository,
        commitments: CommitmentEventRepository,
        workout_plans: WorkoutPlanRepository,
        progress_reviews: ProgressReviewRepository,
        memory: ConversationMemoryRepository,
        plan_overrides: PlanOverrideRepository,
        exercise_baselines: ExerciseBaselineRepository,
        toolkit: CoachToolkit | None = None,
        conversation: ConversationWindow | None = None,
    ) -> None:
        self.settings = settings
        self.toolkit = toolkit
        self.conversation = conversation
        self.prompt_builder = prompt_builder
        self.openai = openai_client
        self.users = users
        self.workouts = workouts
        self.cardio = cardio
        self.nutrition = nutrition
        self.sleep = sleep
        self.measurements = measurements
        self.commitments = commitments
        self.workout_plans = workout_plans
        self.progress_reviews = progress_reviews
        self.memory = memory
        self.plan_overrides = plan_overrides
        self.exercise_baselines = exercise_baselines

    def get_user(self, discord_user_id: str | None = None) -> models.User:
        """Get or create the single application user."""

        return self.users.get_or_create_single_user(
            discord_user_id=discord_user_id,
            timezone=self.settings.timezone,
        )

    def log_workout(self, user_id: str, payload: WorkoutLog) -> CoachResponse:
        """Persist a completed workout event."""

        history = self.workouts.list_for_user(user_id, limit=_PR_HISTORY_LIMIT)
        event = self.workouts.add(
            models.WorkoutEvent(
                user_id=user_id,
                occurred_at=payload.occurred_at,
                workout_type=payload.workout_type,
                duration_minutes=payload.duration_minutes,
                calories_burned=payload.calories_burned,
                perceived_effort=payload.perceived_effort,
                exercises=normalize_exercises(payload.exercises),
                proof_source=payload.proof_source,
                proof_confidence=payload.proof_confidence,
                notes=payload.notes,
            )
        )
        records = find_new_personal_records(history=history, new_exercises=event.exercises)
        message = "Workout logged. Good, practical consistency point for today."
        if records:
            message = f"{message}\n\n{_format_pr_congratulations(records)}"
        baseline_lines = self._check_exercise_baselines(user_id, event.exercises)
        if baseline_lines:
            message = f"{message}\n\n" + "\n".join(baseline_lines)
        message = _append_review(message, _workout_quality_review(event, history, records))
        message = _append_review(message, self._next_time_line(event, history))
        return CoachResponse(
            message=message,
            metadata={
                "event_id": event.id,
                "event_type": "workout_completed",
                "personal_records": [record.exercise for record in records],
            },
        )

    def _next_time_line(
        self, event: models.WorkoutEvent, history: list[models.WorkoutEvent]
    ) -> str:
        """At most one line, and only when the data earns a weight change or an ease-off.

        Routine "add a rep" or "repeat this" guidance is left out: it is the default and
        would only add length.
        """

        recent_first = sorted(
            history, key=lambda workout: _ensure_utc(workout.occurred_at), reverse=True
        )
        for exercise in event.exercises:
            name = str(exercise.get("name", "")).strip()
            if not name:
                continue
            sessions = [exercise] + [
                prior
                for workout in recent_first
                for prior in workout.exercises
                if str(prior.get("name", "")).strip().casefold() == name.casefold()
            ]
            suggestion = suggest_next(
                name,
                sessions,
                rep_targets=self.settings.rep_targets,
                increments=self.settings.equipment_increments,
                hits_to_progress=self.settings.hits_to_progress,
            )
            if suggestion.action in (Action.INCREASE_WEIGHT, Action.EASE_OFF):
                return f"Next time ({name}): {suggestion.message}"
        return ""

    def reclassify_sets(self, user_id: str, args: str) -> CoachResponse:
        """Re-tag sets in the latest workout: ``<exercise> <set numbers> <type>``.

        Only the ``set_type`` key changes; the recorded weights and reps are never touched.
        Any problem is answered in one line and nothing is modified.
        """

        usage = (
            "Use: !setfix <exercise> <set numbers> <warmup|working|backoff>, "
            "e.g. !setfix press 1-2 warmup"
        )
        parts = args.split()
        if len(parts) < 3:
            return _setfix_reply(usage, updated=False)
        set_type = parse_set_type(parts[-1])
        numbers = parse_set_numbers(parts[-2])
        query = " ".join(parts[:-2])
        if set_type is None or numbers is None:
            return _setfix_reply(usage, updated=False)

        latest = self.workouts.recent(user_id, limit=1)
        if not latest:
            return _setfix_reply("No workout logged yet to fix.", updated=False)
        event = latest[0]
        matches = [e for e in event.exercises if _exercise_name_matches(query, e.get("name"))]
        if len(matches) != 1:
            names = ", ".join(str(e.get("name")) for e in event.exercises)
            problem = "matches more than one exercise" if matches else "isn't in your last workout"
            return _setfix_reply(f"'{query}' {problem}. Last workout: {names}.", updated=False)
        target = matches[0]
        sets = target.get("sets")
        if not isinstance(sets, list) or max(numbers) > len(sets):
            count = len(sets) if isinstance(sets, list) else 0
            return _setfix_reply(f"{target.get('name')} has {count} sets.", updated=False)

        wanted = {number - 1 for number in numbers}
        retagged = {
            **target,
            "sets": [
                {**item, "set_type": set_type.value} if index in wanted else dict(item)
                for index, item in enumerate(sets)
            ],
        }
        event.exercises = [retagged if e is target else e for e in event.exercises]
        self.workouts.session.flush()
        rendered = _format_exercise_sets(retagged["sets"])
        return _setfix_reply(f"{target.get('name')}: {rendered}", updated=True)

    def log_cardio(self, user_id: str, payload: CardioLog) -> CoachResponse:
        """Persist a completed cardio event."""

        event = self.cardio.add(models.CardioEvent(user_id=user_id, **payload.model_dump()))
        return CoachResponse(
            message="Cardio logged.",
            metadata={"event_id": event.id, "event_type": "cardio_completed"},
        )

    def log_nutrition(self, user_id: str, payload: NutritionLog) -> CoachResponse:
        """Persist a daily nutrition summary event."""

        event = self.nutrition.add(models.NutritionEvent(user_id=user_id, **payload.model_dump()))
        protein_gap = self.settings.protein_goal_g - payload.protein_g
        if protein_gap > 0:
            message = f"Nutrition logged. Protein was {protein_gap:.0f}g under the current goal."
        else:
            message = "Nutrition logged. Protein target was met."
        return CoachResponse(
            message=message,
            metadata={"event_id": event.id, "event_type": "nutrition_logged"},
        )

    def log_nutrition_remaining(
        self,
        user_id: str,
        *,
        logged_for: datetime,
        calories_remaining: float,
        protein_remaining_g: float,
        carbs_remaining_g: float,
        fat_remaining_g: float,
        proof_source: str | None = None,
        proof_confidence: float | None = None,
        notes: str = "",
    ) -> CoachResponse:
        """Log nutrition from MyFitnessPal-style "remaining" amounts.

        Remaining amounts are relative to the configured daily goals, so consumed
        totals are goal minus remaining (a negative remaining means the goal was
        exceeded).
        """

        return self.log_nutrition(
            user_id,
            NutritionLog(
                logged_for=logged_for,
                calories=round(self.settings.calorie_goal - calories_remaining),
                protein_g=self.settings.protein_goal_g - protein_remaining_g,
                carbs_g=self.settings.carbs_goal_g - carbs_remaining_g,
                fat_g=self.settings.fat_goal_g - fat_remaining_g,
                proof_source=proof_source,
                proof_confidence=proof_confidence,
                notes=notes,
            ),
        )

    def log_sleep(self, user_id: str, payload: SleepLog) -> CoachResponse:
        """Persist a nightly sleep summary event."""

        event = self.sleep.add(models.SleepEvent(user_id=user_id, **payload.model_dump()))
        return CoachResponse(
            message="Sleep logged.",
            metadata={"event_id": event.id, "event_type": "sleep_logged"},
        )

    def create_commitment(self, user_id: str, payload: CommitmentCreate) -> CoachResponse:
        """Create a commitment to follow up on later."""

        event = self.commitments.add(
            models.CommitmentEvent(
                user_id=user_id,
                description=payload.description,
                due_at=payload.due_at,
            )
        )
        return CoachResponse(
            message=(
                "Commitment saved. I’ll use it for follow-up instead of relying on "
                "chat history."
            ),
            should_follow_up=True,
            metadata={"event_id": event.id, "event_type": "commitment_created"},
        )

    def create_plan_override(self, user_id: str, payload: PlanOverrideCreate) -> CoachResponse:
        """Log a short-lived deviation from the default training schedule."""

        event = self.plan_overrides.add(
            models.PlanOverride(
                user_id=user_id,
                description=payload.description,
                starts_on=payload.starts_on,
                expires_on=payload.expires_on,
            )
        )
        return CoachResponse(
            message=(
                f"Noted through {payload.expires_on.isoformat()} - the default schedule "
                "resumes automatically after that."
            ),
            metadata={"event_id": event.id, "event_type": "plan_override_created"},
        )

    def complete_commitment(self, commitment_id: str) -> CoachResponse:
        """Mark a commitment as completed."""

        event = self.commitments.complete(commitment_id)
        return CoachResponse(
            message="Commitment completed.",
            metadata={"event_id": event.id, "event_type": "commitment_completed"},
        )

    def answer_question(
        self, user_id: str, message: str, *, use_routing: bool = True
    ) -> CoachResponse:
        """Answer a free-text message.

        Routed path (default): a deterministic classifier decides which sources the reply
        needs and which chat-safe tools to expose; the model fetches only what it needs
        via function calling; citations are verified against the knowledge base and the
        URLs actually fetched this turn.

        Legacy path (`use_routing=False`, or no toolkit wired): the pre-routing behavior -
        keyword analytics gate, full static prompt, single-shot call, no tools. Kept so
        evals can compare the two on identical scenarios.
        """

        if not use_routing or self.toolkit is None:
            return self._answer_legacy(user_id, message)

        decision = self.route_message(message)
        if decision.category == RouteCategory.LOCKED_ANALYTICS:
            raise AnalyticsLockedError(self._locked_analytics_message(user_id))

        risk = decision.category == RouteCategory.SAFETY
        self.toolkit.bind(user_id, research_allowed=decision.research_allowed, risk=risk)
        injury_record = self._record_pain_report(user_id, decision, message) if risk else None

        prompt = self.build_routed_prompt(user_id, decision)
        tools = self.toolkit.schemas_for(decision.tools)
        result = self.openai.respond(
            system_prompt=prompt,
            user_message=message,
            tools=tools or None,
            tool_executor=self.toolkit.dispatch if tools else None,
            max_tool_rounds=self.settings.max_tool_rounds,
        )

        metadata = dict(result.metadata)
        metadata["route"] = decision.as_dict()
        metadata["tool_calls"] = [call["name"] for call in self.toolkit.calls]
        if self.toolkit.research_results:
            metadata["research"] = [r.as_dict() for r in self.toolkit.research_results]
        if injury_record:
            metadata["injury_record"] = injury_record
        metadata.update(self.citation_report(result.text, external_urls=self.toolkit.external_urls))

        if self.conversation is not None:
            self.conversation.append(user_id, "user", message)
            self.conversation.append(user_id, "assistant", result.text)
        return CoachResponse(message=result.text, metadata=metadata)

    def route_message(self, message: str) -> RoutingDecision:
        """Expose the deterministic routing decision (used by evals and diagnostics)."""

        return classify_message(message, analytics_locked=self.settings.analytics_locked)

    def build_routed_prompt(self, user_id: str, decision: RoutingDecision) -> str:
        sections = [guidance_for(decision)]
        if decision.needs_conversation_context and self.conversation is not None:
            sections.append(self.conversation.format(user_id))
        return self.prompt_builder.build(user_id, extra_sections=sections)

    def _answer_legacy(self, user_id: str, message: str) -> CoachResponse:
        if self._asks_for_locked_analytics(message):
            raise AnalyticsLockedError(self._locked_analytics_message(user_id))
        prompt = self.prompt_builder.build(user_id)
        result = self.openai.respond(system_prompt=prompt, user_message=message)
        metadata = dict(result.metadata)
        metadata["route"] = {"category": "legacy"}
        metadata.update(self.citation_report(result.text, external_urls=set()))
        return CoachResponse(message=result.text, metadata=metadata)

    def _locked_analytics_message(self, user_id: str) -> str:
        base = "Detailed cumulative analytics are locked until the configured review window."
        try:
            due_at = self.next_review_due_at(user_id)
        except Exception:  # noqa: BLE001 - message text must never fail the request
            return base
        if due_at is None:
            return f"{base} A progress review is due now - run !progress to generate it."
        return f"{base} Your next review unlocks on {due_at:%B %d}."

    def _record_pain_report(
        self, user_id: str, decision: RoutingDecision, message: str
    ) -> dict[str, Any] | None:
        """Deterministically log a pain report so it persists whether or not the model does."""

        if self.toolkit is None:
            return None
        areas = decision.entities.get("body_areas") or ["unspecified"]
        try:
            return self.toolkit.record_pain_report(str(areas[0]), message)
        except Exception:  # noqa: BLE001 - never let bookkeeping break the reply
            logger.exception("pain_report_record_failed")
            return None

    def citation_report(self, text: str, *, external_urls: set[str]) -> dict[str, list[str]]:
        """Two-list citation backstop: knowledge-base whitelist plus this turn's fetched URLs.

        - In the knowledge base: fine, unlabeled.
        - Fetched via research this turn: must carry the external label near the URL.
        - Neither: unverified (the model cited something it neither had nor fetched).

        Deterministic and LLM-free. A fabricated claim with no URL attached still isn't
        caught here - that remains the prompt's job.
        """

        cited = {url.rstrip(").,;") for url in _RESPONSE_URL_PATTERN.findall(text)}
        if not cited:
            return {}
        known = self.prompt_builder.known_citation_urls()
        report: dict[str, list[str]] = {}
        unverified = sorted(cited - known - external_urls)
        if unverified:
            logger.warning("Coach response cited unverified URL(s): %s", unverified)
            report["unverified_citation_urls"] = unverified
        mislabeled = sorted(
            url for url in cited & external_urls if not _external_label_precedes(text, url)
        )
        if mislabeled:
            logger.warning("External citation(s) missing label: %s", mislabeled)
            report["mislabeled_external_citations"] = mislabeled
        labeled = sorted((cited & external_urls) - set(mislabeled))
        if labeled:
            report["external_citations"] = labeled
        return report

    def store_vision_extraction(
        self,
        user_id: str,
        extraction: VisionExtraction,
        now: datetime | None = None,
    ) -> CoachResponse:
        """Persist structured facts extracted from an image."""

        now = now or datetime.now(UTC)
        if extraction.needs_clarification or extraction.confidence < 0.5:
            return CoachResponse(
                message=_format_clarification_request(extraction.facts),
                metadata={
                    "event_type": "vision_clarification_required",
                    "confidence": extraction.confidence,
                    "facts": extraction.facts,
                },
            )

        facts = extraction.facts
        notes = json.dumps(facts, sort_keys=True)
        if extraction.kind in (ImageKind.WORKOUT_SCREENSHOT, ImageKind.WORKOUT_TEXT):
            history = self.workouts.list_for_user(user_id, limit=_PR_HISTORY_LIMIT)
            event = self.workouts.add(
                models.WorkoutEvent(
                    user_id=user_id,
                    occurred_at=now,
                    workout_type=str(facts.get("workout_type", "Workout")),
                    duration_minutes=_optional_int(facts.get("duration_minutes")),
                    calories_burned=_optional_int(facts.get("calories_burned")),
                    exercises=normalize_exercises(facts.get("exercises", [])),
                    proof_source=extraction.kind,
                    proof_confidence=extraction.confidence,
                    notes=notes,
                )
            )
            records = find_new_personal_records(history=history, new_exercises=event.exercises)
            message = _format_workout_confirmation(event)
            if records:
                message = f"{message}\n\n{_format_pr_congratulations(records)}"
            baseline_lines = self._check_exercise_baselines(user_id, event.exercises)
            if baseline_lines:
                message = f"{message}\n\n" + "\n".join(baseline_lines)
            message = _append_review(message, _workout_quality_review(event, history, records))
            message = _append_review(message, self._next_time_line(event, history))
            return CoachResponse(
                message=message,
                metadata={
                    "event_id": event.id,
                    "event_type": "workout_completed",
                    "personal_records": [record.exercise for record in records],
                },
            )

        if extraction.kind == ImageKind.CARDIO_SCREENSHOT:
            event = self.cardio.add(
                models.CardioEvent(
                    user_id=user_id,
                    occurred_at=now,
                    modality=str(facts.get("modality", "cardio")),
                    duration_minutes=_optional_int(facts.get("duration_minutes")) or 0,
                    distance_miles=_optional_float(facts.get("distance_miles")),
                    calories_burned=_optional_int(facts.get("calories_burned")),
                    average_heart_rate=_optional_int(facts.get("average_heart_rate")),
                    incline=_optional_float(facts.get("incline")),
                    speed_mph=_optional_float(facts.get("speed_mph")),
                    proof_source=extraction.kind,
                    proof_confidence=extraction.confidence,
                    notes=notes,
                )
            )
            return CoachResponse(
                message=_format_cardio_confirmation(event),
                metadata={"event_id": event.id, "event_type": "cardio_completed"},
            )

        if extraction.kind == ImageKind.NUTRITION_SCREENSHOT:
            required = (
                "calories_remaining",
                "protein_g_remaining",
                "carbs_g_remaining",
                "fat_g_remaining",
            )
            if not all(key in facts for key in required):
                return CoachResponse(
                    message=(
                        "I need the calories, protein, carbs, and fat remaining before "
                        "logging nutrition."
                    ),
                    metadata={"event_type": "vision_clarification_required", "facts": facts},
                )
            result = self.log_nutrition_remaining(
                user_id,
                logged_for=now,
                calories_remaining=float(facts["calories_remaining"]),
                protein_remaining_g=float(facts["protein_g_remaining"]),
                carbs_remaining_g=float(facts["carbs_g_remaining"]),
                fat_remaining_g=float(facts["fat_g_remaining"]),
                proof_source=extraction.kind,
                proof_confidence=extraction.confidence,
                notes=notes,
            )
            return CoachResponse(
                message="Nutrition screenshot processed and stored as structured daily totals.",
                metadata=result.metadata,
            )

        if extraction.kind == ImageKind.SLEEP_SCREENSHOT:
            if "time_asleep_minutes" not in facts:
                return CoachResponse(
                    message="I need at least total time asleep before logging that as sleep data.",
                    metadata={"event_type": "vision_clarification_required", "facts": facts},
                )
            event = self.sleep.add(
                models.SleepEvent(
                    user_id=user_id,
                    logged_for=now,
                    bedtime=_optional_datetime(facts.get("bedtime")),
                    wake_time=_optional_datetime(facts.get("wake_time")),
                    time_asleep_minutes=_optional_int(facts.get("time_asleep_minutes")),
                    time_awake_minutes=_optional_int(facts.get("time_awake_minutes")),
                    light_minutes=_optional_int(facts.get("light_minutes")),
                    deep_minutes=_optional_int(facts.get("deep_minutes")),
                    rem_minutes=_optional_int(facts.get("rem_minutes")),
                    regularity_percent=_optional_float(facts.get("regularity_percent")),
                    sleep_latency_minutes=_optional_int(facts.get("sleep_latency_minutes")),
                    wake_up_mood=facts.get("wake_up_mood"),
                    proof_source=extraction.kind,
                    proof_confidence=extraction.confidence,
                    notes=notes,
                )
            )
            return CoachResponse(
                message="Sleep screenshot processed and stored as a structured nightly summary.",
                metadata={"event_id": event.id, "event_type": "sleep_logged"},
            )

        event = self.measurements.add(
            models.MeasurementEvent(
                user_id=user_id,
                measured_at=now,
                body_weight_lb=_optional_float(facts.get("body_weight_lb")),
                waist_inches=_optional_float(facts.get("waist_inches")),
                body_fat_percent=_optional_float(facts.get("body_fat_percent")),
                progress_photo_path=extraction.retained_path,
                notes=notes,
            )
        )
        return CoachResponse(
            message=str(facts.get("feedback") or "Progress photo saved."),
            metadata={"event_id": event.id, "event_type": "measurement_recorded"},
        )

    def daily_check_in(self, user_id: str) -> CoachResponse:
        """Generate a concise daily check-in prompt."""

        open_commitments = self.commitments.open_for_user(user_id)
        latest_plan = self.workout_plans.latest_for_user(user_id)
        plan_hint = latest_plan.focus if latest_plan else "today’s planned training or cardio"
        commitment_hint = (
            f" You also have {len(open_commitments)} open commitment(s)."
            if open_commitments
            else ""
        )
        return CoachResponse(
            message=(
                f"Check-in: what’s your plan for {plan_hint}? "
                "After training, send proof or a concise summary."
                f"{commitment_hint}"
            ),
            should_follow_up=True,
            metadata={"event_type": "daily_check_in"},
        )

    def last_night_sleep_minutes(self, user_id: str, max_age_hours: int = 18) -> int | None:
        """Duration from a sleep log made in the last ``max_age_hours``, else None.

        The morning message is only built right after sleep is reported, so an older log is
        deliberately not applied to today.
        """

        latest = self.sleep.recent(user_id, limit=1)
        if not latest or latest[0].time_asleep_minutes is None:
            return None
        age = datetime.now(UTC) - _ensure_utc(latest[0].created_at)
        return latest[0].time_asleep_minutes if age <= timedelta(hours=max_age_hours) else None

    def build_review_metrics(self, user_id: str, now: datetime) -> dict[str, Any]:
        """Assemble the deterministic metrics for the review window ending at ``now``."""

        start = now - timedelta(days=self.settings.review_interval_days)
        return build_progress_metrics(
            workouts=self.workouts.between(user_id, start, now),
            cardio_events=self.cardio.between(user_id, start, now),
            nutrition_events=self.nutrition.between(user_id, start, now),
            measurements=self.measurements.between(user_id, start, now),
            sleep_events=self.sleep.between(user_id, start, now),
            start=start,
            end=now,
            today=now.date(),
            protein_goal_g=self.settings.protein_goal_g,
            protein_adherence_threshold_g=self.settings.protein_adherence_threshold_g,
            macro_goals={
                "calories": self.settings.calorie_goal,
                "protein": self.settings.protein_goal_g,
                "carbs": self.settings.carbs_goal_g,
                "fat": self.settings.fat_goal_g,
            },
        )

    def maybe_generate_review(
        self,
        user_id: str,
        metrics: dict[str, Any],
        now: datetime | None = None,
    ) -> models.ProgressReview | None:
        """Create a progress review when the configured interval has elapsed."""

        now = now or datetime.now(UTC)
        latest = self.progress_reviews.latest_for_user(user_id)
        review_interval = timedelta(days=self.settings.review_interval_days)
        if latest and now - _ensure_utc(latest.generated_at) < review_interval:
            return None

        period_start = now - timedelta(days=self.settings.review_interval_days)
        prompt = self.prompt_builder.build(user_id)
        explanation = self.openai.respond(
            system_prompt=prompt,
            user_message=f"{_REVIEW_INSTRUCTIONS}\n{json.dumps(metrics, default=str)}",
        )
        return self.progress_reviews.add(
            models.ProgressReview(
                user_id=user_id,
                period_start=period_start,
                period_end=now,
                metrics=metrics,
                narrative=explanation.text,
            )
        )

    def next_review_due_at(self, user_id: str, now: datetime | None = None) -> datetime | None:
        """Return when the next progress review is due, or None if one is due now."""

        now = now or datetime.now(UTC)
        latest = self.progress_reviews.latest_for_user(user_id)
        if latest is None:
            return None
        interval = timedelta(days=self.settings.review_interval_days)
        due_at = _ensure_utc(latest.generated_at) + interval
        return due_at if due_at > now else None

    def set_exercise_baseline(
        self,
        user_id: str,
        exercise_name: str,
        baseline_weight: float,
        max_weight: float | None = None,
    ) -> CoachResponse:
        """Set (or overwrite) the baseline/max weight the coach judges logged sets against."""

        baseline = self.exercise_baselines.upsert_baseline(
            user_id=user_id,
            exercise_name=exercise_name,
            baseline_weight=baseline_weight,
            max_weight=max_weight,
        )
        weight_str = _format_number(baseline.baseline_weight)
        message = f"Baseline set: {baseline.display_name} — {weight_str}lbs"
        if baseline.max_weight is not None:
            message += f" (max {_format_number(baseline.max_weight)}lbs)"
        return CoachResponse(
            message=message,
            metadata={"event_id": baseline.id, "event_type": "exercise_baseline_set"},
        )

    def list_exercise_baselines(self, user_id: str) -> list[models.ExerciseBaseline]:
        """Return all configured exercise baselines for the user."""

        return self.exercise_baselines.list_for_user(user_id)

    def _check_exercise_baselines(self, user_id: str, exercises: list[dict[str, Any]]) -> list[str]:
        """Judge each newly-logged exercise against its configured baseline, if any.

        Deterministic and Python-templated, mirroring PR detection - no LLM call, so
        this adds no latency and can't hallucinate a verdict. Silently skips any
        exercise with no baseline configured yet.
        """

        lines: list[str] = []
        for name, (weight, _reps) in best_set_among(exercises).items():
            baseline = self.exercise_baselines.get_for_exercise(user_id, name)
            if baseline is None:
                continue

            verdict, near_max = judge_against_baseline(
                logged_weight=weight,
                baseline_weight=baseline.baseline_weight,
                max_weight=baseline.max_weight,
            )
            tracked_weight, streak = next_tracked_weight(
                current_tracked_weight=baseline.tracked_weight,
                current_streak=baseline.consecutive_sessions_at_tracked_weight,
                logged_weight=weight,
            )
            promoted = streak >= PROMOTION_STREAK and tracked_weight != baseline.baseline_weight
            new_baseline_weight = tracked_weight if promoted else baseline.baseline_weight
            self.exercise_baselines.update_tracking(
                baseline.id,
                tracked_weight=tracked_weight,
                consecutive_sessions=1 if promoted else streak,
                baseline_weight=new_baseline_weight,
            )

            lines.append(_format_baseline_status(baseline.display_name, verdict, near_max))
            if promoted:
                lines.append(_format_baseline_promotion(baseline.display_name, new_baseline_weight))
        return lines

    def _asks_for_locked_analytics(self, message: str) -> bool:
        """Legacy keyword gate. The routed path enforces the lock structurally instead."""

        if not self.settings.analytics_locked:
            return False
        lowered = message.lower()
        analytics_terms = ("streak", "average", "trend", "monthly", "progress report", "analytics")
        return any(term in lowered for term in analytics_terms)


def _external_label_precedes(text: str, url: str) -> bool:
    index = text.find(url)
    if index < 0:
        return False
    window = text[max(0, index - _EXTERNAL_LABEL_LOOKBACK) : index]
    return EXTERNAL_LABEL in window


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_datetime(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _ensure_utc(value: datetime) -> datetime:
    """Reattach UTC if missing.

    SQLite doesn't round-trip tzinfo: a DateTime(timezone=True) column reads back
    tz-aware only within the same session that wrote it, and naive once re-fetched
    from a fresh query. Comparing that naive value against datetime.now(UTC) raises
    TypeError, so normalize before any arithmetic.
    """

    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _format_workout_confirmation(event: models.WorkoutEvent) -> str:
    """Build a bulleted summary of what was understood, so the user can spot-check it."""

    header = event.workout_type
    details = []
    if event.duration_minutes:
        details.append(f"{event.duration_minutes} min")
    if event.calories_burned:
        details.append(f"{event.calories_burned} cal")
    if details:
        header += f" ({', '.join(details)})"

    lines = [f"Logged: {header}"]
    for exercise in event.exercises:
        name = exercise.get("name", "Exercise")
        rendered = _format_exercise_sets(exercise.get("sets"))
        lines.append(f"- {name}: {rendered}" if rendered else f"- {name}")
    return "\n".join(lines)


def _setfix_reply(message: str, *, updated: bool) -> CoachResponse:
    return CoachResponse(
        message=message,
        metadata={"event_type": "set_type_updated" if updated else "set_type_unchanged"},
    )


def _exercise_name_matches(query: str, name: Any) -> bool:
    """True when every word typed appears (as a word prefix) in the exercise name."""

    name_words = re.findall(r"[a-z0-9]+", str(name or "").lower())
    query_words = re.findall(r"[a-z0-9]+", query.lower())
    return bool(query_words) and all(
        any(word.startswith(typed) for word in name_words) for typed in query_words
    )


def _format_exercise_sets(sets: Any) -> str:
    """Render sets compactly; warm-ups go in parentheses, lighter back-offs follow an arrow.

    Only distinctions the user can act on are marked: a back-off at the same weight as the
    top set is just another set, so plain straight-set logs read exactly as before.
    """

    if not isinstance(sets, list):
        return ""
    types = classify_sets(sets)
    warmups: list[str] = []
    main: list[str] = []
    backoffs: list[str] = []
    top_weight = max(
        (
            parse_number(item.get("weight")) or 0.0
            for item, kind in zip(sets, types, strict=True)
            if kind != SetType.WARMUP
        ),
        default=0.0,
    )
    lighter_started = False
    for item, kind in zip(sets, types, strict=True):
        part = _format_set(item.get("weight"), item.get("reps"))
        if not part:
            continue
        if kind == SetType.WARMUP:
            warmups.append(part)
            continue
        weight = parse_number(item.get("weight")) or 0.0
        if kind == SetType.BACKOFF and 0 < weight < top_weight:
            lighter_started = True
        (backoffs if lighter_started else main).append(part)

    segments = []
    if warmups:
        segments.append(f"({', '.join(_collapse_repeats(warmups))})")
    if main:
        segments.append(", ".join(_collapse_repeats(main)))
    rendered = " ".join(segments)
    if backoffs:
        rendered = f"{rendered} → {', '.join(_collapse_repeats(backoffs))}".strip()
    return rendered


_QUALITY_CHANGE_THRESHOLD = 0.03
"""A top set must move at least 3% (estimated max) against the recent median to be called."""


def _append_review(message: str, review: str) -> str:
    return f"{message}\n\n{review}" if review else message


def _workout_quality_review(
    event: models.WorkoutEvent,
    history: list[models.WorkoutEvent],
    records: Sequence[PersonalRecord] = (),
) -> str:
    """One grounded sentence about today's top working sets, or "" when there is nothing to say.

    Warm-ups never count as the top set, the comparison uses the five most recent prior
    sessions, and exercises already celebrated as PRs are not repeated. Rep-range
    boilerplate is gone: silence beats a filler line.
    """

    celebrated = {record.exercise.strip().casefold() for record in records}
    recent_first = sorted(
        history, key=lambda workout: _ensure_utc(workout.occurred_at), reverse=True
    )
    changes: list[tuple[float, str]] = []
    compared = 0
    for exercise in event.exercises:
        name = str(exercise.get("name", "")).strip()
        today = top_working_set(exercise)
        if not name or today is None or name.casefold() in celebrated:
            continue
        priors = [
            top
            for workout in recent_first
            for prior in workout.exercises
            if str(prior.get("name", "")).strip().casefold() == name.casefold()
            and (top := top_working_set(prior)) is not None
        ][:5]
        if len(priors) < 2:
            continue
        compared += 1
        scores = sorted(weight * (1 + reps / 30) for weight, reps in priors)
        change = today[0] * (1 + today[1] / 30) / scores[len(scores) // 2] - 1
        if abs(change) < _QUALITY_CHANGE_THRESHOLD:
            continue
        last_weight, last_reps = priors[0]
        direction = "up" if change > 0 else "down"
        changes.append(
            (
                abs(change),
                f"{name}: top set {_format_set(today[0], today[1])}, {direction} from "
                f"{_format_set(last_weight, last_reps)} last session.",
            )
        )
    if changes:
        changes.sort(key=lambda item: item[0], reverse=True)
        return " ".join(text for _, text in changes[:2])
    if compared:
        return "Top sets held steady versus your recent sessions."
    return ""



def _format_cardio_confirmation(event: models.CardioEvent) -> str:
    """Build a summary of what was understood from a cardio screenshot, for spot-checking."""

    details = []
    if event.duration_minutes:
        details.append(f"{event.duration_minutes} min")
    if event.distance_miles:
        details.append(f"{event.distance_miles:g} mi")
    if event.calories_burned:
        details.append(f"{event.calories_burned} cal")
    if event.average_heart_rate:
        details.append(f"{event.average_heart_rate} bpm avg")

    header = event.modality
    if details:
        header += f" ({', '.join(details)})"
    return f"Logged: {header}"


def _format_pr_congratulations(records: list[PersonalRecord]) -> str:
    """Format a calm, factual note for newly logged personal records.

    Keeps praise earned rather than hyped, per the Communication Style principles -
    just the exercise and the numbers, not exclamation points.
    """

    header = "New PR:" if len(records) == 1 else "New PRs:"
    lines = [header]
    for record in records:
        previous_weight = _format_number(record.previous_weight)
        previous_reps = _format_number(record.previous_reps)
        current = f"{_format_number(record.weight)}lbs x{_format_number(record.reps)}"
        previous = f"{previous_weight}lbs x{previous_reps}"
        lines.append(f"- {record.exercise}: {current} (up from {previous})")
    return "\n".join(lines)


_VERDICT_LABELS = {"under": "Under", "good": "Good", "over": "Over"}


def _format_baseline_status(exercise: str, verdict: str, near_max: bool) -> str:
    line = f"Status: {exercise} — {_VERDICT_LABELS[verdict]}"
    if near_max:
        line += " (near your max - not necessary every session for sustainable growth)"
    return line


def _format_baseline_promotion(exercise: str, new_baseline_weight: float) -> str:
    weight = _format_number(new_baseline_weight)
    return f"Baseline updated: {exercise} is now {weight}lbs after 5 sessions at that weight."


_MAX_CLARIFICATION_EXERCISES = 10


def _format_clarification_request(facts: dict[str, Any]) -> str:
    """Build a clarification message that shows whatever was actually extracted.

    The bare "I need a clarification" message gives no way to tell what went wrong -
    surfacing the partial facts lets the user see what was read correctly (so they
    know what's missing/wrong) instead of hitting a dead end.
    """

    header = "I need a clarification before logging that as proof."
    exercises = facts.get("exercises")
    if isinstance(exercises, list) and exercises:
        names = [
            str(exercise.get("name", "?")) for exercise in exercises if isinstance(exercise, dict)
        ]
        if len(names) > _MAX_CLARIFICATION_EXERCISES:
            shown = names[:_MAX_CLARIFICATION_EXERCISES]
            names_text = f"{', '.join(shown)}, and {len(names) - len(shown)} more"
        else:
            names_text = ", ".join(names)
        return f"{header} Here's what I could make out: {names_text}."

    parts = [f"{key}: {value}" for key, value in facts.items() if value not in (None, "", [])]
    if parts:
        return f"{header} Here's what I could make out: {'; '.join(parts)}."
    return (
        f"{header} I couldn't read anything usable from that - try resending a clearer "
        "photo, or type it out directly instead."
    )


def _collapse_repeats(values: list[str]) -> list[str]:
    """Collapse consecutive identical entries (e.g. matching right/left-arm sets)."""

    collapsed: list[tuple[str, int]] = []
    for value in values:
        if collapsed and collapsed[-1][0] == value:
            collapsed[-1] = (value, collapsed[-1][1] + 1)
        else:
            collapsed.append((value, 1))
    return [f"{value} ×{count}" if count > 1 else value for value, count in collapsed]


def _format_set(weight: Any, reps: Any) -> str | None:
    weight_str = _format_number(weight)
    reps_str = _format_number(reps)
    if weight_str is not None and reps_str is not None:
        return f"{weight_str}lbs x{reps_str}"
    if reps_str is not None:
        return f"x{reps_str}"
    if weight_str is not None:
        return f"{weight_str}lbs"
    return None


_LEADING_NUMBER = re.compile(r"[-+]?\d*\.?\d+")


def _format_number(value: Any) -> str | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        # The vision model sometimes bakes units into the value (e.g. "30 lbs" instead
        # of 30); pull the leading number out rather than displaying the raw string.
        match = _LEADING_NUMBER.search(str(value))
        if not match:
            return None
        number = float(match.group())
    return str(int(number)) if number == int(number) else str(number)
