"""Core coaching orchestration."""

from __future__ import annotations

import json
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
    UserRepository,
    WorkoutEventRepository,
    WorkoutPlanRepository,
)
from fitness_coach.database.schemas import (
    CardioLog,
    CoachResponse,
    CommitmentCreate,
    NutritionLog,
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
                message="I need a clarification before logging that image as proof.",
                metadata={
                    "event_type": "vision_clarification_required",
                    "confidence": extraction.confidence,
                    "facts": extraction.facts,
                },
            )

        facts = extraction.facts
        notes = json.dumps(facts, sort_keys=True)
        if extraction.kind == ImageKind.WORKOUT_SCREENSHOT:
            event = self.workouts.add(
                models.WorkoutEvent(
                    user_id=user_id,
                    occurred_at=now,
                    workout_type=str(facts.get("workout_type", "Workout")),
                    duration_minutes=_optional_int(facts.get("duration_minutes")),
                    exercises=list(facts.get("exercises", [])),
                    proof_source=extraction.kind,
                    proof_confidence=extraction.confidence,
                    notes=notes,
                )
            )
            return CoachResponse(
                message="Workout proof processed and stored as structured workout data.",
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
            required = ("calories", "protein_g", "carbs_g", "fat_g")
            if not all(key in facts for key in required):
                return CoachResponse(
                    message=(
                        "I need the calories, protein, carbs, and fat before logging "
                        "nutrition."
                    ),
                    metadata={"event_type": "vision_clarification_required", "facts": facts},
                )
            event = self.nutrition.add(
                models.NutritionEvent(
                    user_id=user_id,
                    logged_for=now,
                    calories=int(facts["calories"]),
                    protein_g=float(facts["protein_g"]),
                    carbs_g=float(facts["carbs_g"]),
                    fat_g=float(facts["fat_g"]),
                    proof_source=extraction.kind,
                    proof_confidence=extraction.confidence,
                    notes=notes,
                )
            )
            return CoachResponse(
                message="Nutrition screenshot processed and stored as structured daily totals.",
                metadata={"event_id": event.id, "event_type": "nutrition_logged"},
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
