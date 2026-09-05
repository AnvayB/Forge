"""Repository layer that isolates business logic from SQLAlchemy queries."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from fitness_coach.database import models


class Repository[ModelT: models.Base]:
    """Small generic repository for model-specific repositories."""

    model: type[ModelT]

    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, entity: ModelT) -> ModelT:
        self.session.add(entity)
        self.session.flush()
        return entity

    def get(self, entity_id: str) -> ModelT | None:
        return self.session.get(self.model, entity_id)

    def list_for_user(self, user_id: str, limit: int = 100) -> list[ModelT]:
        stmt = select(self.model).where(self.model.user_id == user_id).limit(limit)  # type: ignore[attr-defined]
        return list(self.session.scalars(stmt))


class UserRepository(Repository[models.User]):
    """Repository for user records."""

    model = models.User

    def get_or_create_single_user(
        self,
        *,
        discord_user_id: str | None = None,
        display_name: str = "Anvay",
        timezone: str = "America/Los_Angeles",
    ) -> models.User:
        stmt = select(models.User).limit(1)
        user = self.session.scalars(stmt).first()
        if user is not None:
            return user
        return self.add(
            models.User(
                discord_user_id=discord_user_id,
                display_name=display_name,
                timezone=timezone,
            )
        )


class WorkoutEventRepository(Repository[models.WorkoutEvent]):
    """Repository for workout completion events."""

    model = models.WorkoutEvent

    def recent(self, user_id: str, limit: int = 10) -> list[models.WorkoutEvent]:
        stmt = (
            select(models.WorkoutEvent)
            .where(models.WorkoutEvent.user_id == user_id)
            .order_by(models.WorkoutEvent.occurred_at.desc())
            .limit(limit)
        )
        return list(self.session.scalars(stmt))

    def between(
        self, user_id: str, start: datetime, end: datetime
    ) -> list[models.WorkoutEvent]:
        stmt = select(models.WorkoutEvent).where(
            models.WorkoutEvent.user_id == user_id,
            models.WorkoutEvent.occurred_at >= start,
            models.WorkoutEvent.occurred_at <= end,
        )
        return list(self.session.scalars(stmt))


class CardioEventRepository(Repository[models.CardioEvent]):
    """Repository for cardio completion events."""

    model = models.CardioEvent

    def recent(self, user_id: str, limit: int = 10) -> list[models.CardioEvent]:
        stmt = (
            select(models.CardioEvent)
            .where(models.CardioEvent.user_id == user_id)
            .order_by(models.CardioEvent.occurred_at.desc())
            .limit(limit)
        )
        return list(self.session.scalars(stmt))

    def between(self, user_id: str, start: datetime, end: datetime) -> list[models.CardioEvent]:
        stmt = select(models.CardioEvent).where(
            models.CardioEvent.user_id == user_id,
            models.CardioEvent.occurred_at >= start,
            models.CardioEvent.occurred_at <= end,
        )
        return list(self.session.scalars(stmt))


class NutritionEventRepository(Repository[models.NutritionEvent]):
    """Repository for daily nutrition summary events."""

    model = models.NutritionEvent

    def recent(self, user_id: str, limit: int = 10) -> list[models.NutritionEvent]:
        stmt = (
            select(models.NutritionEvent)
            .where(models.NutritionEvent.user_id == user_id)
            .order_by(models.NutritionEvent.logged_for.desc())
            .limit(limit)
        )
        return list(self.session.scalars(stmt))

    def between(
        self, user_id: str, start: datetime, end: datetime
    ) -> list[models.NutritionEvent]:
        stmt = select(models.NutritionEvent).where(
            models.NutritionEvent.user_id == user_id,
            models.NutritionEvent.logged_for >= start,
            models.NutritionEvent.logged_for <= end,
        )
        return list(self.session.scalars(stmt))


class SleepEventRepository(Repository[models.SleepEvent]):
    """Repository for nightly sleep summary events."""

    model = models.SleepEvent

    def recent(self, user_id: str, limit: int = 7) -> list[models.SleepEvent]:
        stmt = (
            select(models.SleepEvent)
            .where(models.SleepEvent.user_id == user_id)
            .order_by(models.SleepEvent.logged_for.desc())
            .limit(limit)
        )
        return list(self.session.scalars(stmt))

    def between(self, user_id: str, start: datetime, end: datetime) -> list[models.SleepEvent]:
        stmt = select(models.SleepEvent).where(
            models.SleepEvent.user_id == user_id,
            models.SleepEvent.logged_for >= start,
            models.SleepEvent.logged_for <= end,
        )
        return list(self.session.scalars(stmt))


class MeasurementEventRepository(Repository[models.MeasurementEvent]):
    """Repository for body measurement events."""

    model = models.MeasurementEvent

    def between(
        self, user_id: str, start: datetime, end: datetime
    ) -> list[models.MeasurementEvent]:
        stmt = select(models.MeasurementEvent).where(
            models.MeasurementEvent.user_id == user_id,
            models.MeasurementEvent.measured_at >= start,
            models.MeasurementEvent.measured_at <= end,
        )
        return list(self.session.scalars(stmt))

    def latest_with_photo_for_user(self, user_id: str) -> models.MeasurementEvent | None:
        """Return the most recent measurement event that has a retained progress photo."""

        stmt = (
            select(models.MeasurementEvent)
            .where(
                models.MeasurementEvent.user_id == user_id,
                models.MeasurementEvent.progress_photo_path.is_not(None),
            )
            .order_by(models.MeasurementEvent.measured_at.desc())
            .limit(1)
        )
        return self.session.scalars(stmt).first()


class CommitmentEventRepository(Repository[models.CommitmentEvent]):
    """Repository for accountability commitments."""

    model = models.CommitmentEvent

    def open_for_user(self, user_id: str) -> list[models.CommitmentEvent]:
        stmt = select(models.CommitmentEvent).where(
            models.CommitmentEvent.user_id == user_id,
            models.CommitmentEvent.status == models.CommitmentStatus.OPEN,
        )
        return list(self.session.scalars(stmt))

    def complete(
        self, commitment_id: str, completed_at: datetime | None = None
    ) -> models.CommitmentEvent:
        commitment = self.session.get(models.CommitmentEvent, commitment_id)
        if commitment is None:
            raise ValueError(f"Unknown commitment: {commitment_id}")
        commitment.status = models.CommitmentStatus.COMPLETED
        commitment.completed_at = completed_at or datetime.now(UTC)
        self.session.flush()
        return commitment


class PlanOverrideRepository(Repository[models.PlanOverride]):
    """Repository for short-lived schedule overrides."""

    model = models.PlanOverride

    def active_for_user(self, user_id: str, on_date: date) -> list[models.PlanOverride]:
        stmt = select(models.PlanOverride).where(
            models.PlanOverride.user_id == user_id,
            models.PlanOverride.starts_on <= on_date,
            models.PlanOverride.expires_on >= on_date,
        )
        return list(self.session.scalars(stmt))


class WorkoutPlanRepository(Repository[models.WorkoutPlan]):
    """Repository for generated workout plans."""

    model = models.WorkoutPlan

    def latest_for_user(self, user_id: str) -> models.WorkoutPlan | None:
        stmt = (
            select(models.WorkoutPlan)
            .where(models.WorkoutPlan.user_id == user_id)
            .order_by(models.WorkoutPlan.generated_at.desc())
            .limit(1)
        )
        return self.session.scalars(stmt).first()


class ProgressReviewRepository(Repository[models.ProgressReview]):
    """Repository for periodic progress reviews."""

    model = models.ProgressReview

    def latest_for_user(self, user_id: str) -> models.ProgressReview | None:
        stmt = (
            select(models.ProgressReview)
            .where(models.ProgressReview.user_id == user_id)
            .order_by(models.ProgressReview.generated_at.desc())
            .limit(1)
        )
        return self.session.scalars(stmt).first()


class ConversationMemoryRepository(Repository[models.ConversationMemory]):
    """Repository for summarized durable memory."""

    model = models.ConversationMemory

    def all_for_user(self, user_id: str) -> list[models.ConversationMemory]:
        stmt = (
            select(models.ConversationMemory)
            .where(models.ConversationMemory.user_id == user_id)
            .order_by(models.ConversationMemory.key.asc())
        )
        return list(self.session.scalars(stmt))

    def upsert_fact(
        self,
        *,
        user_id: str,
        key: str,
        value: dict[str, Any],
        confidence: float = 1.0,
        source: str = "conversation",
    ) -> models.ConversationMemory:
        stmt = select(models.ConversationMemory).where(
            models.ConversationMemory.user_id == user_id,
            models.ConversationMemory.key == key,
        )
        fact = self.session.scalars(stmt).first()
        if fact is None:
            fact = models.ConversationMemory(
                user_id=user_id,
                key=key,
                value=value,
                confidence=confidence,
                source=source,
            )
            return self.add(fact)

        fact.value = value
        fact.confidence = confidence
        fact.source = source
        fact.updated_at = datetime.now(UTC)
        self.session.flush()
        return fact


class GoalRepository(Repository[models.Goal]):
    """Repository for structured goals."""

    model = models.Goal


class InjuryHistoryRepository(Repository[models.InjuryHistory]):
    """Repository for injury history."""

    model = models.InjuryHistory

    def active_for_user(self, user_id: str) -> list[models.InjuryHistory]:
        stmt = select(models.InjuryHistory).where(
            models.InjuryHistory.user_id == user_id,
            models.InjuryHistory.status != models.InjuryStatus.RESOLVED,
        )
        return list(self.session.scalars(stmt))


class CoachNoteRepository(Repository[models.CoachNote]):
    """Repository for durable coach notes."""

    model = models.CoachNote

    def active_for_user(self, user_id: str) -> list[models.CoachNote]:
        stmt = select(models.CoachNote).where(
            models.CoachNote.user_id == user_id,
            models.CoachNote.active.is_(True),
        )
        return list(self.session.scalars(stmt))


def _normalize_exercise_name(name: str) -> str:
    return name.strip().lower()


class ExerciseBaselineRepository(Repository[models.ExerciseBaseline]):
    """Repository for per-exercise baseline/max weight tracking."""

    model = models.ExerciseBaseline

    def get_for_exercise(self, user_id: str, exercise_name: str) -> models.ExerciseBaseline | None:
        stmt = select(models.ExerciseBaseline).where(
            models.ExerciseBaseline.user_id == user_id,
            models.ExerciseBaseline.exercise_name == _normalize_exercise_name(exercise_name),
        )
        return self.session.scalars(stmt).first()

    def upsert_baseline(
        self,
        *,
        user_id: str,
        exercise_name: str,
        baseline_weight: float,
        max_weight: float | None = None,
    ) -> models.ExerciseBaseline:
        """Set (or overwrite) a baseline. Overwriting resets streak tracking."""

        baseline = self.get_for_exercise(user_id, exercise_name)
        if baseline is None:
            baseline = models.ExerciseBaseline(
                user_id=user_id,
                exercise_name=_normalize_exercise_name(exercise_name),
                display_name=exercise_name.strip(),
                baseline_weight=baseline_weight,
                max_weight=max_weight,
            )
            return self.add(baseline)

        baseline.display_name = exercise_name.strip()
        baseline.baseline_weight = baseline_weight
        baseline.max_weight = max_weight
        baseline.tracked_weight = None
        baseline.consecutive_sessions_at_tracked_weight = 0
        baseline.updated_at = datetime.now(UTC)
        self.session.flush()
        return baseline

    def update_tracking(
        self,
        baseline_id: str,
        *,
        tracked_weight: float,
        consecutive_sessions: int,
        baseline_weight: float,
    ) -> models.ExerciseBaseline:
        baseline = self.session.get(models.ExerciseBaseline, baseline_id)
        if baseline is None:
            raise ValueError(f"Unknown exercise baseline: {baseline_id}")
        baseline.tracked_weight = tracked_weight
        baseline.consecutive_sessions_at_tracked_weight = consecutive_sessions
        baseline.baseline_weight = baseline_weight
        baseline.updated_at = datetime.now(UTC)
        self.session.flush()
        return baseline
