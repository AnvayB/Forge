"""Normalized SQLAlchemy models for the coach event store."""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""

    return datetime.now(UTC)


def new_id() -> str:
    """Return a stable string identifier."""

    return str(uuid.uuid4())


class Base(DeclarativeBase):
    """Base class for all database models."""


class CommitmentStatus(enum.StrEnum):
    """Lifecycle states for commitments."""

    OPEN = "open"
    COMPLETED = "completed"
    MISSED = "missed"
    CANCELLED = "cancelled"


class GoalStatus(enum.StrEnum):
    """Lifecycle states for goals."""

    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class InjuryStatus(enum.StrEnum):
    """Lifecycle states for injury records."""

    ACTIVE = "active"
    MONITORING = "monitoring"
    RESOLVED = "resolved"


class User(Base):
    """Application user. The first version expects one user, but keeps IDs explicit."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    discord_user_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(120), default="Anvay")
    timezone: Mapped[str] = mapped_column(String(64), default="America/Los_Angeles")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    workouts: Mapped[list[WorkoutEvent]] = relationship(back_populates="user")
    cardio_sessions: Mapped[list[CardioEvent]] = relationship(back_populates="user")
    nutrition_logs: Mapped[list[NutritionEvent]] = relationship(back_populates="user")
    measurements: Mapped[list[MeasurementEvent]] = relationship(back_populates="user")
    commitments: Mapped[list[CommitmentEvent]] = relationship(back_populates="user")


class WorkoutPlan(Base):
    """Structured workout plan generated for a date or training focus."""

    __tablename__ = "workout_plans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    split_day: Mapped[str] = mapped_column(String(80))
    focus: Mapped[str] = mapped_column(String(160))
    duration_minutes: Mapped[int | None] = mapped_column(Integer)
    exercises: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    rationale: Mapped[str] = mapped_column(Text, default="")
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class WorkoutEvent(Base):
    """Completed workout event extracted from check-ins or proof screenshots."""

    __tablename__ = "workout_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    plan_id: Mapped[str | None] = mapped_column(ForeignKey("workout_plans.id"))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    workout_type: Mapped[str] = mapped_column(String(120))
    duration_minutes: Mapped[int | None] = mapped_column(Integer)
    perceived_effort: Mapped[int | None] = mapped_column(Integer)
    exercises: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    proof_source: Mapped[str | None] = mapped_column(String(80))
    proof_confidence: Mapped[float | None] = mapped_column(Float)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    user: Mapped[User] = relationship(back_populates="workouts")


class CardioEvent(Base):
    """Completed cardio event."""

    __tablename__ = "cardio_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    modality: Mapped[str] = mapped_column(String(120))
    duration_minutes: Mapped[int] = mapped_column(Integer)
    distance_miles: Mapped[float | None] = mapped_column(Float)
    average_heart_rate: Mapped[int | None] = mapped_column(Integer)
    incline: Mapped[float | None] = mapped_column(Float)
    speed_mph: Mapped[float | None] = mapped_column(Float)
    proof_source: Mapped[str | None] = mapped_column(String(80))
    proof_confidence: Mapped[float | None] = mapped_column(Float)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    user: Mapped[User] = relationship(back_populates="cardio_sessions")


class NutritionEvent(Base):
    """Daily nutrition totals copied from an external tracker."""

    __tablename__ = "nutrition_events"
    __table_args__ = (UniqueConstraint("user_id", "logged_for", name="uq_nutrition_user_day"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    logged_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    calories: Mapped[int] = mapped_column(Integer)
    protein_g: Mapped[float] = mapped_column(Float)
    carbs_g: Mapped[float] = mapped_column(Float)
    fat_g: Mapped[float] = mapped_column(Float)
    proof_source: Mapped[str | None] = mapped_column(String(80))
    proof_confidence: Mapped[float | None] = mapped_column(Float)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    user: Mapped[User] = relationship(back_populates="nutrition_logs")


class MeasurementEvent(Base):
    """Body measurement event."""

    __tablename__ = "measurement_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    body_weight_lb: Mapped[float | None] = mapped_column(Float)
    waist_inches: Mapped[float | None] = mapped_column(Float)
    body_fat_percent: Mapped[float | None] = mapped_column(Float)
    progress_photo_path: Mapped[str | None] = mapped_column(String(500))
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    user: Mapped[User] = relationship(back_populates="measurements")


class CommitmentEvent(Base):
    """Commitment lifecycle event for accountability follow-up."""

    __tablename__ = "commitment_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[CommitmentStatus] = mapped_column(
        Enum(CommitmentStatus), default=CommitmentStatus.OPEN, index=True
    )
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    user: Mapped[User] = relationship(back_populates="commitments")


class ProgressReview(Base):
    """Periodic review generated from deterministic metrics plus LLM explanation."""

    __tablename__ = "progress_reviews"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    narrative: Mapped[str] = mapped_column(Text, default="")
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ConversationMemory(Base):
    """Summarized durable memory facts derived from conversations."""

    __tablename__ = "conversation_memory"
    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_memory_user_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    key: Mapped[str] = mapped_column(String(120))
    value: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    source: Mapped[str] = mapped_column(String(120), default="conversation")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Goal(Base):
    """Structured user goal."""

    __tablename__ = "goals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[GoalStatus] = mapped_column(Enum(GoalStatus), default=GoalStatus.ACTIVE)
    target_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class InjuryHistory(Base):
    """Structured injury or discomfort history."""

    __tablename__ = "injury_history"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    body_area: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[InjuryStatus] = mapped_column(
        Enum(InjuryStatus), default=InjuryStatus.MONITORING
    )
    severity: Mapped[int | None] = mapped_column(Integer)
    first_noted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    last_noted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class CoachNote(Base):
    """Internal structured note the coach can use for future context."""

    __tablename__ = "coach_notes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    category: Mapped[str] = mapped_column(String(120))
    note: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
