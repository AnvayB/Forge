"""APScheduler job registration."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from discord.ext import commands

from fitness_coach.analytics.reports import build_progress_metrics
from fitness_coach.coach.factory import ServiceFactory
from fitness_coach.coach.service import CoachService
from fitness_coach.database.repositories import (
    CardioEventRepository,
    MeasurementEventRepository,
    NutritionEventRepository,
    UserRepository,
    WorkoutEventRepository,
)

logger = logging.getLogger(__name__)


def register_jobs(
    scheduler: BackgroundScheduler, factory: ServiceFactory, bot: commands.Bot
) -> None:
    """Register configured recurring jobs."""

    timezone = ZoneInfo(factory.coach_settings.timezone)

    morning_time = factory.coach_settings.morning_workout_reminder_time
    morning_hour, morning_minute = _parse_time(morning_time)
    scheduler.add_job(
        run_morning_check_in,
        CronTrigger(hour=morning_hour, minute=morning_minute, timezone=timezone),
        args=[factory, bot],
        id="morning_check_in",
        replace_existing=True,
    )

    evening_hour, evening_minute = _parse_time(factory.coach_settings.daily_checkin_time)
    scheduler.add_job(
        run_evening_macros_check_in,
        CronTrigger(hour=evening_hour, minute=evening_minute, timezone=timezone),
        args=[factory, bot],
        id="evening_macros_check_in",
        replace_existing=True,
    )
    scheduler.add_job(
        run_progress_review,
        CronTrigger(hour=evening_hour, minute=min(59, evening_minute + 5), timezone=timezone),
        args=[factory],
        id="progress_review",
        replace_existing=True,
    )


def build_workout_text(coach: CoachService, user_id: str, timezone: str) -> str:
    """Ask the coach for today's scheduled workout, per the Morning Workout Message format.

    Kept separate from the morning check-in so it can also be sent once the user
    actually reports their sleep, instead of appearing in both messages.
    """

    today = datetime.now(ZoneInfo(timezone)).strftime("%A, %B %d")
    workout_prompt = (
        f"Today is {today}. Following the Morning Workout Message format from your "
        "instructions, tell me today's scheduled workout: the workout type, the five or "
        "six exercises, sets, rep ranges, and brief guidance on intensity, form, rest, "
        "and progression."
    )
    return coach.answer_question(user_id, workout_prompt).message


def run_morning_check_in(factory: ServiceFactory, bot: commands.Bot) -> None:
    """DM the user asking how they slept. Today's workout follows once they reply."""

    with factory.session() as session:
        coach = factory.coach_service(session)
        user = coach.get_user()
        discord_user_id = user.discord_user_id

    if not discord_user_id:
        logger.warning("morning_check_in_skipped_no_discord_user")
        return

    message = (
        "Good morning! How'd you sleep last night? Send your SleepCycle screenshot "
        '(mention "sleep") or just reply with total hours/minutes asleep.'
    )
    _dispatch_dm(bot, discord_user_id, message)


def run_evening_macros_check_in(factory: ServiceFactory, bot: commands.Bot) -> None:
    """DM the user asking for today's macros."""

    with factory.session() as session:
        coach = factory.coach_service(session)
        user = coach.get_user()
        discord_user_id = user.discord_user_id

    if not discord_user_id:
        logger.warning("evening_check_in_skipped_no_discord_user")
        return

    message = (
        "Evening check-in — what were your macros today? Send calories, protein, carbs, "
        "and fat, or a MyFitnessPal screenshot."
    )
    _dispatch_dm(bot, discord_user_id, message)


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
        measurements = MeasurementEventRepository(session).between(user.id, start, now)
        metrics = build_progress_metrics(
            workouts=workouts,
            cardio_events=cardio,
            nutrition_events=nutrition,
            measurements=measurements,
            start=start,
            end=now,
            today=now.date(),
            protein_goal_g=factory.coach_settings.protein_goal_g,
        )
        review = factory.coach_service(session).maybe_generate_review(user.id, metrics, now)
        if review:
            logger.info("progress_review_generated user_id=%s review_id=%s", user.id, review.id)


def build_scheduler(factory: ServiceFactory, bot: commands.Bot) -> BackgroundScheduler:
    """Build a configured background scheduler."""

    scheduler = BackgroundScheduler(timezone=factory.coach_settings.timezone)
    register_jobs(scheduler, factory, bot)
    return scheduler


def _parse_time(value: str) -> tuple[int, int]:
    hour_str, minute_str = value.split(":")
    return int(hour_str), int(minute_str)


async def _send_dm(bot: commands.Bot, discord_user_id: str, message: str) -> None:
    user = await bot.fetch_user(int(discord_user_id))
    await user.send(message)


def _dispatch_dm(bot: commands.Bot, discord_user_id: str, message: str) -> None:
    """Send a DM from a non-async APScheduler thread via the bot's event loop."""

    future = asyncio.run_coroutine_threadsafe(_send_dm(bot, discord_user_id, message), bot.loop)
    try:
        future.result(timeout=30)
    except Exception:
        logger.exception("scheduled_dm_failed discord_user_id=%s", discord_user_id)
