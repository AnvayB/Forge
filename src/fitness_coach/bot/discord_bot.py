"""Thin discord.py adapter."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from pathlib import Path

import discord
from discord.ext import commands

from fitness_coach.coach.factory import ServiceFactory
from fitness_coach.coach.service import AnalyticsLockedError
from fitness_coach.config.settings import get_app_settings, get_coach_settings
from fitness_coach.database.schemas import (
    CardioLog,
    CommitmentCreate,
    SleepLog,
)
from fitness_coach.logging import configure_logging
from fitness_coach.scheduler.jobs import build_scheduler, build_workout_text
from fitness_coach.vision.processor import ImageKind

logger = logging.getLogger(__name__)


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
    async def cardio(ctx: commands.Context[commands.Bot], minutes: int, *, modality: str) -> None:
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
