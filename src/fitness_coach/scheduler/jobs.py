"""APScheduler job registration."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from fitness_coach.analytics.reports import build_progress_metrics
from fitness_coach.coach.factory import ServiceFactory
from fitness_coach.database.repositories import (
    CardioEventRepository,
    NutritionEventRepository,
    UserRepository,
    WorkoutEventRepository,
)

logger = logging.getLogger(__name__)


def register_jobs(scheduler: BackgroundScheduler, factory: ServiceFactory) -> None:
    """Register configured recurring jobs."""

    timezone = ZoneInfo(factory.coach_settings.timezone)
    hour, minute = [int(part) for part in factory.coach_settings.daily_checkin_time.split(":")]
    scheduler.add_job(
        run_daily_check_in,
        CronTrigger(hour=hour, minute=minute, timezone=timezone),
        args=[factory],
        id="daily_check_in",
        replace_existing=True,
    )
    scheduler.add_job(
        run_progress_review,
        CronTrigger(hour=hour, minute=min(59, minute + 5), timezone=timezone),
        args=[factory],
        id="progress_review",
        replace_existing=True,
    )


def run_daily_check_in(factory: ServiceFactory) -> None:
    """Generate the daily check-in message for downstream delivery."""

    with factory.session() as session:
        coach = factory.coach_service(session)
        user = coach.get_user()
        response = coach.daily_check_in(user.id)
        logger.info("daily_check_in_ready user_id=%s message=%s", user.id, response.message)


def run_progress_review(factory: ServiceFactory) -> None:
    """Generate a review if the configured interval has elapsed."""

    with factory.session() as session:
        user = UserRepository(session).get_or_create_single_user(
            timezone=factory.coach_settings.timezone
        )
        now = datetime.now(UTC)
        start = now - timedelta(days=factory.coach_settings.review_interval_days)
        workouts = WorkoutEventRepository(session).between(user.id, start, now)
        cardio = CardioEventRepository(session).between(user.id, start, now)
        nutrition = NutritionEventRepository(session).between(user.id, start, now)
        metrics = build_progress_metrics(
            workouts=workouts,
            cardio_events=cardio,
            nutrition_events=nutrition,
            start=start,
            end=now,
            today=now.date(),
            protein_goal_g=factory.coach_settings.protein_goal_g,
        )
        review = factory.coach_service(session).maybe_generate_review(user.id, metrics, now)
        if review:
            logger.info("progress_review_generated user_id=%s review_id=%s", user.id, review.id)


def build_scheduler(factory: ServiceFactory) -> BackgroundScheduler:
    """Build a configured background scheduler."""

    scheduler = BackgroundScheduler(timezone=factory.coach_settings.timezone)
    register_jobs(scheduler, factory)
    return scheduler
