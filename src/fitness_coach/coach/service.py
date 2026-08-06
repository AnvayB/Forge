"""Core coaching orchestration."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from fitness_coach.coach.openai_client import CoachOpenAIClient
from fitness_coach.config.prompt_builder import PromptBuilder
from fitness_coach.config.settings import CoachSettings
from fitness_coach.database import models
from fitness_coach.database.repositories import (
    CardioEventRepository,
    CommitmentEventRepository,
    ConversationMemoryRepository,
    MeasurementEventRepository,
    NutritionEventRepository,
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
    SleepLog,
    WorkoutLog,
)
from fitness_coach.vision.processor import ImageKind, VisionExtraction


class AnalyticsLockedError(PermissionError):
    """Raised when a request would reveal locked cumulative analytics."""


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
    ) -> None:
        self.settings = settings
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

    def get_user(self, discord_user_id: str | None = None) -> models.User:
        """Get or create the single application user."""

        return self.users.get_or_create_single_user(
            discord_user_id=discord_user_id,
            timezone=self.settings.timezone,
        )

    def log_workout(self, user_id: str, payload: WorkoutLog) -> CoachResponse:
        """Persist a completed workout event."""

        event = self.workouts.add(
            models.WorkoutEvent(
                user_id=user_id,
                occurred_at=payload.occurred_at,
                workout_type=payload.workout_type,
                duration_minutes=payload.duration_minutes,
                calories_burned=payload.calories_burned,
                perceived_effort=payload.perceived_effort,
                exercises=payload.exercises,
                proof_source=payload.proof_source,
                proof_confidence=payload.proof_confidence,
                notes=payload.notes,
            )
        )
        return CoachResponse(
            message="Workout logged. Good, practical consistency point for today.",
            metadata={"event_id": event.id, "event_type": "workout_completed"},
        )

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

    def complete_commitment(self, commitment_id: str) -> CoachResponse:
        """Mark a commitment as completed."""

        event = self.commitments.complete(commitment_id)
        return CoachResponse(
            message="Commitment completed.",
            metadata={"event_id": event.id, "event_type": "commitment_completed"},
        )

    def answer_question(self, user_id: str, message: str) -> CoachResponse:
        """Answer lightweight coaching questions using PromptBuilder."""

        if self._asks_for_locked_analytics(message):
            raise AnalyticsLockedError(
                "Detailed cumulative analytics are locked until the configured review window."
            )
        prompt = self.prompt_builder.build(user_id)
        result = self.openai.respond(system_prompt=prompt, user_message=message)
        return CoachResponse(message=result.text, metadata=result.metadata)

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
                message="I need a clarification before logging that as proof.",
                metadata={
                    "event_type": "vision_clarification_required",
                    "confidence": extraction.confidence,
                    "facts": extraction.facts,
                },
            )

        facts = extraction.facts
        notes = json.dumps(facts, sort_keys=True)
        if extraction.kind in (ImageKind.WORKOUT_SCREENSHOT, ImageKind.WORKOUT_TEXT):
            event = self.workouts.add(
                models.WorkoutEvent(
                    user_id=user_id,
                    occurred_at=now,
                    workout_type=str(facts.get("workout_type", "Workout")),
                    duration_minutes=_optional_int(facts.get("duration_minutes")),
                    calories_burned=_optional_int(facts.get("calories_burned")),
                    exercises=list(facts.get("exercises", [])),
                    proof_source=extraction.kind,
                    proof_confidence=extraction.confidence,
                    notes=notes,
                )
            )
            return CoachResponse(
                message=_format_workout_confirmation(event),
                metadata={"event_id": event.id, "event_type": "workout_completed"},
            )

        if extraction.kind == ImageKind.CARDIO_SCREENSHOT:
            event = self.cardio.add(
                models.CardioEvent(
                    user_id=user_id,
                    occurred_at=now,
                    modality=str(facts.get("modality", "cardio")),
                    duration_minutes=_optional_int(facts.get("duration_minutes")) or 0,
                    distance_miles=_optional_float(facts.get("distance_miles")),
                    average_heart_rate=_optional_int(facts.get("average_heart_rate")),
                    incline=_optional_float(facts.get("incline")),
                    speed_mph=_optional_float(facts.get("speed_mph")),
                    proof_source=extraction.kind,
                    proof_confidence=extraction.confidence,
                    notes=notes,
                )
            )
            return CoachResponse(
                message="Cardio proof processed and stored as structured cardio data.",
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
            message="Progress photo retained and measurement event stored.",
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
        if latest and now - latest.generated_at < review_interval:
            return None

        period_start = now - timedelta(days=self.settings.review_interval_days)
        prompt = self.prompt_builder.build(user_id)
        explanation = self.openai.respond(
            system_prompt=prompt,
            user_message=(
                "Explain these deterministic progress-review metrics concisely. "
                "Do not calculate new analytics.\n"
                f"{metrics}"
            ),
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

    def _asks_for_locked_analytics(self, message: str) -> bool:
        if not self.settings.analytics_locked:
            return False
        lowered = message.lower()
        analytics_terms = ("streak", "average", "trend", "monthly", "progress report", "analytics")
        return any(term in lowered for term in analytics_terms)


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
        set_strs = [
            part
            for exercise_set in exercise.get("sets", [])
            if (part := _format_set(exercise_set.get("weight"), exercise_set.get("reps")))
        ]
        rendered = ", ".join(_collapse_repeats(set_strs))
        lines.append(f"- {name}: {rendered}" if rendered else f"- {name}")
    return "\n".join(lines)


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
