"""Pydantic schemas used at service and interface boundaries."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class WorkoutLog(BaseModel):
    """Input schema for completed workouts."""

    occurred_at: datetime
    workout_type: str
    duration_minutes: int | None = None
    calories_burned: int | None = Field(default=None, ge=0)
    perceived_effort: int | None = Field(default=None, ge=1, le=10)
    exercises: list[dict[str, Any]] = Field(default_factory=list)
    proof_source: str | None = None
    proof_confidence: float | None = Field(default=None, ge=0, le=1)
    notes: str = ""


class CardioLog(BaseModel):
    """Input schema for completed cardio."""

    occurred_at: datetime
    modality: str
    duration_minutes: int = Field(gt=0)
    distance_miles: float | None = Field(default=None, ge=0)
    average_heart_rate: int | None = Field(default=None, ge=0)
    incline: float | None = None
    speed_mph: float | None = Field(default=None, ge=0)
    proof_source: str | None = None
    proof_confidence: float | None = Field(default=None, ge=0, le=1)
    notes: str = ""


class NutritionLog(BaseModel):
    """Input schema for daily nutrition totals."""

    logged_for: datetime
    calories: int = Field(ge=0)
    protein_g: float = Field(ge=0)
    carbs_g: float = Field(ge=0)
    fat_g: float = Field(ge=0)
    proof_source: str | None = None
    proof_confidence: float | None = Field(default=None, ge=0, le=1)
    notes: str = ""


class SleepLog(BaseModel):
    """Input schema for a nightly sleep summary.

    `logged_for` should be the date of the morning the summary covers (the
    wake-up day), since sleep screenshots are provided each morning for the
    prior night.
    """

    logged_for: datetime
    bedtime: datetime | None = None
    wake_time: datetime | None = None
    time_asleep_minutes: int | None = Field(default=None, ge=0)
    time_awake_minutes: int | None = Field(default=None, ge=0)
    light_minutes: int | None = Field(default=None, ge=0)
    deep_minutes: int | None = Field(default=None, ge=0)
    rem_minutes: int | None = Field(default=None, ge=0)
    regularity_percent: float | None = Field(default=None, ge=0, le=100)
    sleep_latency_minutes: int | None = Field(default=None, ge=0)
    wake_up_mood: str | None = None
    proof_source: str | None = None
    proof_confidence: float | None = Field(default=None, ge=0, le=1)
    notes: str = ""


class MeasurementLog(BaseModel):
    """Input schema for body measurements."""

    measured_at: datetime
    body_weight_lb: float | None = Field(default=None, ge=0)
    waist_inches: float | None = Field(default=None, ge=0)
    body_fat_percent: float | None = Field(default=None, ge=0, le=100)
    progress_photo_path: str | None = None
    notes: str = ""


class CommitmentCreate(BaseModel):
    """Input schema for new commitments."""

    description: str
    due_at: datetime | None = None


class PlanOverrideCreate(BaseModel):
    """Input schema for a short-lived deviation from the default training schedule."""

    description: str
    starts_on: date
    expires_on: date


class MemoryFact(BaseModel):
    """Input schema for summarized durable memory."""

    key: str
    value: dict[str, Any]
    confidence: float = Field(default=1.0, ge=0, le=1)
    source: str = "conversation"


class WorkoutPlanRequest(BaseModel):
    """Inputs used to adapt the deterministic workout plan."""

    requested_for: datetime
    available_minutes: int | None = None
    soreness: list[str] = Field(default_factory=list)
    injuries: list[str] = Field(default_factory=list)
    recovery_notes: str = ""


class CoachResponse(BaseModel):
    """Structured response returned to Discord/API adapters."""

    message: str
    should_follow_up: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")
