"""Runtime settings loaded from environment variables and coach YAML config."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class CoachSettings(BaseSettings):
    """Settings authored in `config/coach_settings.yaml`."""

    timezone: str = "America/Los_Angeles"
    review_interval_days: int = 30
    analytics_locked: bool = True
    daily_checkin_time: str = "22:00"
    morning_workout_reminder_time: str = "10:00"
    cardio_days: list[str] = Field(default_factory=lambda: ["Monday", "Wednesday", "Friday"])
    default_cardio_goal_minutes: int = 35
    target_checkpoint_date: str = "2026-12-01"
    calorie_goal: int = 2335
    protein_goal_g: int = 160
    protein_adherence_threshold_g: int = 145
    carbs_goal_g: int = 300
    fat_goal_g: int = 55
    preferred_model: str = "gpt-5.5"
    response_style: str = "concise"
    workout_proof_required: bool = True
    nutrition_checkin_required: bool = True
    progress_photo_interval_days: int = 30
    measurement_interval_days: int = 30
    retain_processed_screenshots: bool = False
    processed_screenshot_retention_days: int = 0
    retain_progress_photos: bool = True


class AppSettings(BaseSettings):
    """Environment-backed application settings."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    discord_token: str | None = None
    openai_api_key: str | None = None
    database_url: str = "sqlite:///data/fitness_coach.db"
    config_dir: Path = Field(default=Path("config"))
    uploads_dir: Path = Field(default=Path("uploads"))
    log_level: str = "INFO"


def load_coach_settings(config_dir: Path) -> CoachSettings:
    """Load typed coach settings from the YAML config file."""

    settings_path = config_dir / "coach_settings.yaml"
    if not settings_path.exists():
        return CoachSettings()

    with settings_path.open("r", encoding="utf-8") as handle:
        raw: dict[str, Any] = yaml.safe_load(handle) or {}
    return CoachSettings(**raw)


@lru_cache(maxsize=1)
def get_app_settings() -> AppSettings:
    """Return cached application settings."""

    return AppSettings()


@lru_cache(maxsize=1)
def get_coach_settings() -> CoachSettings:
    """Return cached coach settings."""

    return load_coach_settings(get_app_settings().config_dir)
