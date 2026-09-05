"""Thin discord.py adapter."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands

from fitness_coach.analytics.reports import build_progress_metrics
from fitness_coach.coach.factory import ServiceFactory
from fitness_coach.coach.service import AnalyticsLockedError
from fitness_coach.config.settings import get_app_settings, get_coach_settings
from fitness_coach.database.repositories import (
    CardioEventRepository,
    MeasurementEventRepository,
    NutritionEventRepository,
    ProgressReviewRepository,
    SleepEventRepository,
    WorkoutEventRepository,
)
from fitness_coach.database.schemas import (
    CardioLog,
    CommitmentCreate,
    PlanOverrideCreate,
    SleepLog,
)
from fitness_coach.logging import configure_logging
from fitness_coach.scheduler.jobs import build_scheduler, build_workout_text
from fitness_coach.vision.processor import ImageKind

logger = logging.getLogger(__name__)

_DISCORD_MESSAGE_LIMIT = 2000


async def _reply_in_chunks(ctx: commands.Context[commands.Bot], text: str) -> None:
    """Reply with `text`, splitting on line breaks to stay under Discord's message limit.

    An LLM-generated review narrative can exceed 2000 characters, which discord.py
    rejects outright - splitting keeps `!progress` from crashing on a long review.
    """

    chunks: list[str] = []
    chunk = ""
    for line in text.splitlines(keepends=True):
        while len(line) > _DISCORD_MESSAGE_LIMIT:
            if chunk:
                chunks.append(chunk)
                chunk = ""
            chunks.append(line[:_DISCORD_MESSAGE_LIMIT])
            line = line[_DISCORD_MESSAGE_LIMIT:]
        if len(chunk) + len(line) > _DISCORD_MESSAGE_LIMIT:
            chunks.append(chunk)
            chunk = ""
        chunk += line
    if chunk:
        chunks.append(chunk)

    for index, part in enumerate(chunks):
        if index == 0:
            await ctx.reply(part)
        else:
            await ctx.send(part)


def build_bot(factory: ServiceFactory) -> commands.Bot:
    """Build a Discord bot that delegates business logic to services."""

    intents = discord.Intents.default()
    intents.message_content = True
    bot = commands.Bot(command_prefix="!", intents=intents)
    scheduler_state: dict[str, object] = {}

    @bot.event
    async def on_ready() -> None:
        logger.info("discord_bot_ready user=%s", bot.user)
        if "scheduler" not in scheduler_state:
            scheduler = build_scheduler(factory, bot)
            scheduler.start()
            scheduler_state["scheduler"] = scheduler
            logger.info("scheduler_started")
            await _send_startup_confirmation(factory, bot)

    @bot.command(name="checkin")
    async def checkin(ctx: commands.Context[commands.Bot]) -> None:
        with factory.session() as session:
            coach = factory.coach_service(session)
            user = coach.get_user(str(ctx.author.id))
            response = coach.daily_check_in(user.id)
        await ctx.reply(response.message)

    @bot.command(name="workout")
    async def workout(ctx: commands.Context[commands.Bot], *, summary: str | None = None) -> None:
        """Log a workout from text, or from one or more screenshots (Arrow, Apple Fitness)."""

        image_attachments = [a for a in ctx.message.attachments if _is_image_attachment(a)]
        if not image_attachments:
            if not summary:
                await ctx.reply(
                    "Send a workout summary (e.g. `!workout Upper body, 45 min`) or attach "
                    "your Arrow / Apple Fitness screenshot(s)."
                )
                return
            with factory.session() as session:
                coach = factory.coach_service(session)
                processor = factory.vision_processor(session)
                user = coach.get_user(str(ctx.author.id))
                extraction = processor.process_workout_text(user_id=user.id, text=summary)
                response = coach.store_vision_extraction(user.id, extraction)
            await ctx.reply(response.message)
            return

        incoming_dir = factory.app_settings.uploads_dir / "tmp"
        incoming_dir.mkdir(parents=True, exist_ok=True)
        saved_paths: list[Path] = []
        try:
            for attachment in image_attachments:
                path = incoming_dir / f"discord_{attachment.id}_{attachment.filename}"
                await attachment.save(path)
                saved_paths.append(path)
            with factory.session() as session:
                coach = factory.coach_service(session)
                processor = factory.vision_processor(session)
                user = coach.get_user(str(ctx.author.id))
                extraction = processor.process_workout_screenshots(
                    user_id=user.id,
                    source_paths=saved_paths,
                    extra_notes=summary or "",
                )
                response = coach.store_vision_extraction(user.id, extraction)
        finally:
            for path in saved_paths:
                path.unlink(missing_ok=True)
        await ctx.reply(response.message)

    @bot.command(name="cardio")
    async def cardio(ctx: commands.Context[commands.Bot], *, args: str | None = None) -> None:
        """Log cardio from text (e.g. `!cardio 20 run`) or a screenshot (Apple Fitness, Garmin)."""

        image_attachments = [a for a in ctx.message.attachments if _is_image_attachment(a)]
        if not image_attachments:
            minutes, modality = _parse_cardio_args(args)
            with factory.session() as session:
                coach = factory.coach_service(session)
                user = coach.get_user(str(ctx.author.id))
                response = coach.log_cardio(
                    user.id,
                    CardioLog(
                        occurred_at=datetime.now(UTC),
                        modality=modality,
                        duration_minutes=minutes,
                        notes="Logged from Discord text command.",
                    ),
                )
            await ctx.reply(response.message)
            return

        incoming_dir = factory.app_settings.uploads_dir / "tmp"
        incoming_dir.mkdir(parents=True, exist_ok=True)
        saved_paths: list[Path] = []
        try:
            for attachment in image_attachments:
                path = incoming_dir / f"discord_{attachment.id}_{attachment.filename}"
                await attachment.save(path)
                saved_paths.append(path)
            with factory.session() as session:
                coach = factory.coach_service(session)
                processor = factory.vision_processor(session)
                user = coach.get_user(str(ctx.author.id))
                extraction = processor.process_cardio_screenshots(
                    user_id=user.id,
                    source_paths=saved_paths,
                    extra_notes=args or "",
                )
                response = coach.store_vision_extraction(user.id, extraction)
        finally:
            for path in saved_paths:
                path.unlink(missing_ok=True)
        await ctx.reply(response.message)

    @bot.command(name="nutrition")
    async def nutrition(
        ctx: commands.Context[commands.Bot],
        carbs_remaining_g: str,
        fat_remaining_g: str,
        protein_remaining_g: str,
        calories_remaining: str,
    ) -> None:
        """Log nutrition from the "remaining" amounts shown in MyFitnessPal.

        Accepts plain numbers or numbers with units (e.g. `188g`, `877cals`).
        """

        with factory.session() as session:
            coach = factory.coach_service(session)
            user = coach.get_user(str(ctx.author.id))
            response = coach.log_nutrition_remaining(
                user.id,
                logged_for=datetime.now(UTC),
                calories_remaining=_parse_macro_value(calories_remaining),
                protein_remaining_g=_parse_macro_value(protein_remaining_g),
                carbs_remaining_g=_parse_macro_value(carbs_remaining_g),
                fat_remaining_g=_parse_macro_value(fat_remaining_g),
                notes="Logged from Discord text command.",
            )
        await ctx.reply(response.message)

    @bot.command(name="sleep")
    async def sleep(
        ctx: commands.Context[commands.Bot], duration: str, *, notes: str = ""
    ) -> None:
        """Log sleep as plain minutes (`398`) or an hours/minutes duration (`6h38m`)."""

        time_asleep_minutes = _parse_sleep_duration(duration)
        with factory.session() as session:
            coach = factory.coach_service(session)
            user = coach.get_user(str(ctx.author.id))
            response = coach.log_sleep(
                user.id,
                SleepLog(
                    logged_for=datetime.now(UTC),
                    time_asleep_minutes=time_asleep_minutes,
                    notes=notes or "Logged from Discord text command.",
                ),
            )
        await ctx.reply(response.message)

    @bot.event
    async def on_command_error(
        ctx: commands.Context[commands.Bot], error: commands.CommandError
    ) -> None:
        if isinstance(error, commands.CommandNotFound):
            return
        cause = error.original if isinstance(error, commands.CommandInvokeError) else error
        if isinstance(cause, commands.UserInputError):
            await ctx.reply(str(cause))
            return
        logger.exception("command_error", exc_info=error)
        await ctx.reply("Something went wrong running that command.")

    @bot.command(name="commit")
    async def commit(ctx: commands.Context[commands.Bot], *, description: str) -> None:
        with factory.session() as session:
            coach = factory.coach_service(session)
            user = coach.get_user(str(ctx.author.id))
            response = coach.create_commitment(user.id, CommitmentCreate(description=description))
        await ctx.reply(response.message)

    @bot.command(name="adjust")
    async def adjust(ctx: commands.Context[commands.Bot], days: int, *, description: str) -> None:
        """Log a short-lived deviation from the default schedule (e.g. moving a workout day).

        Applies from today through `days` day(s) from now, inclusive, then the default
        schedule in training_preferences.md resumes automatically - no need to clear it.
        """

        if days < 1:
            raise commands.BadArgument("`days` must be at least 1.")
        with factory.session() as session:
            coach = factory.coach_service(session)
            user = coach.get_user(str(ctx.author.id))
            today = datetime.now(ZoneInfo(factory.coach_settings.timezone)).date()
            response = coach.create_plan_override(
                user.id,
                PlanOverrideCreate(
                    description=description,
                    starts_on=today,
                    expires_on=today + timedelta(days=days - 1),
                ),
            )
        await ctx.reply(response.message)

    @bot.command(name="setbaseline")
    async def setbaseline(
        ctx: commands.Context[commands.Bot], weight: str, *, exercise: str
    ) -> None:
        """Set the baseline (and optionally max) weight the coach judges logged sets against.

        Use `!setbaseline 135 Bench Press` for baseline only, or `!setbaseline 135/185
        Bench Press` for baseline/max. Overwriting an existing baseline restarts streak
        tracking toward the next auto-promotion.
        """

        baseline_weight, max_weight = _parse_baseline_weight(weight)
        with factory.session() as session:
            coach = factory.coach_service(session)
            user = coach.get_user(str(ctx.author.id))
            response = coach.set_exercise_baseline(user.id, exercise, baseline_weight, max_weight)
        await ctx.reply(response.message)

    @bot.command(name="baselines")
    async def baselines(ctx: commands.Context[commands.Bot]) -> None:
        """List your configured exercise baselines."""

        with factory.session() as session:
            coach = factory.coach_service(session)
            user = coach.get_user(str(ctx.author.id))
            rows = coach.list_exercise_baselines(user.id)
        if not rows:
            await ctx.reply(
                "No exercise baselines set yet. Use `!setbaseline 135 Bench Press` to add one."
            )
            return
        lines = ["**Exercise Baselines**"]
        for row in sorted(rows, key=lambda r: r.display_name.lower()):
            line = f"- {row.display_name}: {row.baseline_weight:g}lbs"
            if row.max_weight is not None:
                line += f" (max {row.max_weight:g}lbs)"
            if row.consecutive_sessions_at_tracked_weight:
                line += (
                    f" — {row.consecutive_sessions_at_tracked_weight}/5 sessions "
                    "toward next update"
                )
            lines.append(line)
        await ctx.reply("\n".join(lines))

    @bot.command(name="progress")
    async def progress(ctx: commands.Context[commands.Bot]) -> None:
        """Deliver the periodic progress review, on its configured cadence only.

        Analytics are intentionally locked between review periods (see the Analytics
        Philosophy in coach_principles.md), so this does not compute fresh stats on
        every call - it only surfaces a review once one is actually due.
        """

        with factory.session() as session:
            coach = factory.coach_service(session)
            user = coach.get_user(str(ctx.author.id))
            now = datetime.now(UTC)
            due_at = coach.next_review_due_at(user.id, now)
            if due_at is not None:
                days_left = (due_at - now).days + 1
                await ctx.reply(
                    f"Your next progress review isn't due for about {days_left} more "
                    "day(s) - analytics stay locked until then by design."
                )
                return

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
            review = coach.maybe_generate_review(user.id, metrics, now)
        if review is None:
            await ctx.reply("No progress review is available right now.")
            return
        await _reply_in_chunks(ctx, review.narrative)

    @bot.command(name="lastreview")
    async def lastreview(ctx: commands.Context[commands.Bot]) -> None:
        """Re-deliver the most recently generated progress review, bypassing the cadence lock.

        A review is persisted as soon as it's generated, before its narrative is sent
        back to Discord - so a delivery failure after generation (e.g. a reply that's
        too long) can leave a review stuck in the database with no way to see it again,
        since `!progress` considers the review period already used. This surfaces
        whatever was last generated, it does not compute anything new.
        """

        with factory.session() as session:
            coach = factory.coach_service(session)
            user = coach.get_user(str(ctx.author.id))
            review = ProgressReviewRepository(session).latest_for_user(user.id)
        if review is None:
            await ctx.reply("No progress review has been generated yet.")
            return
        await _reply_in_chunks(ctx, review.narrative)

    @bot.command(name="recent")
    async def recent(ctx: commands.Context[commands.Bot], count: int = 5) -> None:
        """Show the most recently logged entries per category, straight from the database.

        This is a raw event listing (a sanity check that logging is working), not an
        analytics view - no streaks, averages, or trends, since those stay locked until
        the scheduled progress review (see `!progress`).
        """

        count = max(1, min(count, 20))
        tz = ZoneInfo(factory.coach_settings.timezone)
        with factory.session() as session:
            coach = factory.coach_service(session)
            user = coach.get_user(str(ctx.author.id))
            workouts = WorkoutEventRepository(session).recent(user.id, limit=count)
            cardio = CardioEventRepository(session).recent(user.id, limit=count)
            nutrition = NutritionEventRepository(session).recent(user.id, limit=count)
            sleep = SleepEventRepository(session).recent(user.id, limit=count)

        sections = [
            _format_recent_section(
                "Workouts",
                workouts,
                tz,
                timestamp_of=lambda e: e.occurred_at,
                detail_of=lambda e: e.workout_type
                + (f" ({e.duration_minutes} min)" if e.duration_minutes else ""),
            ),
            _format_recent_section(
                "Cardio",
                cardio,
                tz,
                timestamp_of=lambda e: e.occurred_at,
                detail_of=lambda e: f"{e.modality} ({e.duration_minutes} min)",
            ),
            _format_recent_section(
                "Nutrition",
                nutrition,
                tz,
                timestamp_of=lambda e: e.logged_for,
                detail_of=lambda e: f"{e.calories} cal, {e.protein_g:.0f}g protein",
            ),
            _format_recent_section(
                "Sleep",
                sleep,
                tz,
                timestamp_of=lambda e: e.logged_for,
                detail_of=lambda e: _format_sleep_minutes(e.time_asleep_minutes),
            ),
        ]
        await ctx.reply("\n\n".join(sections))

    @bot.event
    async def on_message(message: discord.Message) -> None:
        if message.author.bot:
            return
        await bot.process_commands(message)
        if message.content.startswith("!"):
            return
        if message.attachments:
            replies: list[str] = []
            logged_sleep = False
            for attachment in message.attachments:
                if not _is_image_attachment(attachment):
                    continue
                incoming_dir = factory.app_settings.uploads_dir / "tmp"
                incoming_dir.mkdir(parents=True, exist_ok=True)
                incoming_path = incoming_dir / f"discord_{attachment.id}_{attachment.filename}"
                await attachment.save(incoming_path)
                try:
                    kind = _infer_image_kind(message.content, attachment.filename)
                    with factory.session() as session:
                        coach = factory.coach_service(session)
                        processor = factory.vision_processor(session)
                        user = coach.get_user(str(message.author.id))
                        if kind is None:
                            extraction = processor.process_auto(
                                user_id=user.id, source_path=incoming_path
                            )
                        elif kind == ImageKind.PROGRESS_PHOTO:
                            previous = coach.measurements.latest_with_photo_for_user(user.id)
                            extraction = processor.process_progress_photo(
                                user_id=user.id,
                                source_path=incoming_path,
                                previous_photo_path=(
                                    previous.progress_photo_path if previous else None
                                ),
                                previous_measured_at=previous.measured_at if previous else None,
                            )
                        else:
                            extraction = processor.process(
                                user_id=user.id,
                                source_path=incoming_path,
                                kind=kind,
                            )
                        response = coach.store_vision_extraction(user.id, extraction)
                        if (
                            extraction.kind == ImageKind.SLEEP_SCREENSHOT
                            and response.metadata.get("event_type") == "sleep_logged"
                        ):
                            logged_sleep = True
                    replies.append(response.message)
                finally:
                    incoming_path.unlink(missing_ok=True)
            if logged_sleep:
                with factory.session() as session:
                    coach = factory.coach_service(session)
                    user = coach.get_user(str(message.author.id))
                    replies.append(
                        build_workout_text(coach, user.id, factory.coach_settings.timezone)
                    )
            if replies:
                await message.reply("\n".join(replies))
                return
        sleep_minutes = _try_parse_sleep_duration(message.content)
        if sleep_minutes is not None:
            with factory.session() as session:
                coach = factory.coach_service(session)
                user = coach.get_user(str(message.author.id))
                response = coach.log_sleep(
                    user.id,
                    SleepLog(
                        logged_for=datetime.now(UTC),
                        time_asleep_minutes=sleep_minutes,
                        notes="Logged from Discord free-text reply.",
                    ),
                )
                workout_text = build_workout_text(coach, user.id, factory.coach_settings.timezone)
            await message.reply(f"{response.message}\n\n{workout_text}")
            return
        with factory.session() as session:
            coach = factory.coach_service(session)
            user = coach.get_user(str(message.author.id))
            try:
                response = coach.answer_question(user.id, message.content)
            except AnalyticsLockedError as error:
                response_text = str(error)
            else:
                response_text = response.message
        await message.reply(response_text)

    return bot


async def _send_startup_confirmation(factory: ServiceFactory, bot: commands.Bot) -> None:
    """DM the user once per process start to confirm the bot is back online."""

    with factory.session() as session:
        coach = factory.coach_service(session)
        user = coach.get_user()
        discord_user_id = user.discord_user_id

    if not discord_user_id:
        logger.warning("startup_confirmation_skipped_no_discord_user")
        return

    try:
        discord_user = await bot.fetch_user(int(discord_user_id))
        await discord_user.send("Fitness coach bot is back online after a redeploy.")
    except discord.DiscordException:
        logger.exception("startup_confirmation_failed")


def run() -> None:
    """Run the Discord bot."""

    app_settings = get_app_settings()
    coach_settings = get_coach_settings()
    configure_logging(app_settings.log_level)
    if not app_settings.discord_token:
        raise RuntimeError("DISCORD_TOKEN must be set")
    factory = ServiceFactory(app_settings, coach_settings)
    bot = build_bot(factory)
    bot.run(app_settings.discord_token)


_SLEEP_DURATION_PATTERN = re.compile(r"^(?:(?P<hours>\d+)h)?(?:(?P<minutes>\d+)m)?$", re.IGNORECASE)


def _try_parse_sleep_duration(raw: str) -> int | None:
    """Parse plain minutes ("398") or "6h38m" / "6h" / "38m", or None if it doesn't match.

    Used to recognize a bare reply to the morning sleep check-in (e.g. "7h39m" with no
    command prefix) without misfiring on unrelated plain-text messages.
    """

    raw = raw.strip()
    if not raw:
        return None
    if raw.isdigit():
        return int(raw)
    match = _SLEEP_DURATION_PATTERN.match(raw)
    if not match or not (match.group("hours") or match.group("minutes")):
        return None
    hours = int(match.group("hours") or 0)
    minutes = int(match.group("minutes") or 0)
    return hours * 60 + minutes


def _parse_sleep_duration(raw: str) -> int:
    """Parse a sleep duration given as plain minutes ("398") or "6h38m" / "6h" / "38m"."""

    minutes = _try_parse_sleep_duration(raw)
    if minutes is None:
        raise commands.BadArgument(
            f'Could not parse "{raw.strip()}" as a sleep duration. Use minutes (e.g. `398`) '
            "or hours/minutes (e.g. `6h38m`, `6h`, `38m`)."
        )
    return minutes


def _parse_cardio_args(raw: str | None) -> tuple[int, str]:
    """Parse "<minutes> <modality>" text args for `!cardio` (e.g. "20 run")."""

    text = (raw or "").strip()
    if not text:
        raise commands.BadArgument(
            "Send `!cardio <minutes> <modality>` (e.g. `!cardio 20 run`) or attach your "
            "cardio screenshot (Apple Fitness, Garmin, etc.)."
        )
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        raise commands.BadArgument("`modality` is a required argument that is missing.")
    minutes_raw, modality = parts
    try:
        minutes = int(minutes_raw)
    except ValueError as error:
        raise commands.BadArgument(
            f'Could not parse "{minutes_raw}" as minutes. Use a whole number (e.g. `20`).'
        ) from error
    return minutes, modality


_MACRO_VALUE_PATTERN = re.compile(r"[-+]?\d*\.?\d+")


def _parse_macro_value(raw: str) -> float:
    """Parse a macro amount that may carry units (e.g. "188g", "-15g", "877cals")."""

    match = _MACRO_VALUE_PATTERN.search(raw)
    if not match:
        raise commands.BadArgument(
            f'Could not parse "{raw.strip()}" as a number. Use a plain number, with or '
            "without units (e.g. `188`, `188g`, `877cals`)."
        )
    return float(match.group())


def _parse_baseline_weight(raw: str) -> tuple[float, float | None]:
    """Parse "<baseline>" or "<baseline>/<max>" (e.g. "135" or "135/185")."""

    parts = raw.strip().split("/")
    if len(parts) not in (1, 2):
        raise commands.BadArgument(
            f'Could not parse "{raw.strip()}" as a weight. Use `135` or `135/185` '
            "(baseline/max)."
        )
    try:
        baseline_weight = float(parts[0])
        max_weight = float(parts[1]) if len(parts) == 2 else None
    except ValueError as error:
        raise commands.BadArgument(
            f'Could not parse "{raw.strip()}" as a weight. Use `135` or `135/185` '
            "(baseline/max)."
        ) from error
    return baseline_weight, max_weight


def _format_recent_section(
    title: str,
    events: list[Any],
    tz: ZoneInfo,
    *,
    timestamp_of: Callable[[Any], datetime],
    detail_of: Callable[[Any], str],
) -> str:
    if not events:
        return f"**{title}**\nNothing logged yet."
    lines = [f"**{title}**"]
    for event in events:
        local_time = timestamp_of(event).astimezone(tz)
        lines.append(f"- {local_time.strftime('%a %b %d, %I:%M %p')} — {detail_of(event)}")
    return "\n".join(lines)


def _format_sleep_minutes(minutes: int | None) -> str:
    if minutes is None:
        return "no duration recorded"
    return f"{minutes // 60}h {minutes % 60}m"


def _is_image_attachment(attachment: discord.Attachment) -> bool:
    content_type = attachment.content_type or ""
    suffix = Path(attachment.filename).suffix.lower()
    return content_type.startswith("image/") or suffix in {".jpg", ".jpeg", ".png", ".webp"}


def _infer_image_kind(message_content: str, filename: str) -> str | None:
    """Guess the screenshot kind from message text/filename, or None if unclear.

    Returning None (rather than defaulting to some kind) lets the caller fall back to
    asking the vision model to classify the image itself instead of guessing wrong.
    """

    text = f"{message_content} {filename}".lower()
    if "sleep" in text:
        return ImageKind.SLEEP_SCREENSHOT
    if "progress" in text or "photo" in text:
        return ImageKind.PROGRESS_PHOTO
    if "nutrition" in text or "myfitnesspal" in text or "macro" in text:
        return ImageKind.NUTRITION_SCREENSHOT
    if "cardio" in text or "treadmill" in text or "garmin" in text or "run" in text:
        return ImageKind.CARDIO_SCREENSHOT
    if "workout" in text or "arrow" in text or "strength" in text or "lift" in text:
        return ImageKind.WORKOUT_SCREENSHOT
    return None
