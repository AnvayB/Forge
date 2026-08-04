"""Thin discord.py adapter."""

from __future__ import annotations

import logging
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
    NutritionLog,
    SleepLog,
    WorkoutLog,
)
from fitness_coach.logging import configure_logging
from fitness_coach.scheduler.jobs import build_scheduler
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

    @bot.command(name="checkin")
    async def checkin(ctx: commands.Context[commands.Bot]) -> None:
        with factory.session() as session:
            coach = factory.coach_service(session)
            user = coach.get_user(str(ctx.author.id))
            response = coach.daily_check_in(user.id)
        await ctx.reply(response.message)

    @bot.command(name="workout")
    async def workout(ctx: commands.Context[commands.Bot], *, summary: str) -> None:
        with factory.session() as session:
            coach = factory.coach_service(session)
            user = coach.get_user(str(ctx.author.id))
            response = coach.log_workout(
                user.id,
                WorkoutLog(
                    occurred_at=datetime.now(UTC),
                    workout_type=summary,
                    notes="Logged from Discord text command.",
                ),
            )
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
        calories: int,
        protein_g: float,
        carbs_g: float,
        fat_g: float,
    ) -> None:
        with factory.session() as session:
            coach = factory.coach_service(session)
            user = coach.get_user(str(ctx.author.id))
            response = coach.log_nutrition(
                user.id,
                NutritionLog(
                    logged_for=datetime.now(UTC),
                    calories=calories,
                    protein_g=protein_g,
                    carbs_g=carbs_g,
                    fat_g=fat_g,
                    notes="Logged from Discord text command.",
                ),
            )
        await ctx.reply(response.message)

    @bot.command(name="sleep")
    async def sleep(
        ctx: commands.Context[commands.Bot], time_asleep_minutes: int, *, notes: str = ""
    ) -> None:
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
                        extraction = processor.process(
                            user_id=user.id,
                            source_path=incoming_path,
                            kind=kind,
                        )
                        response = coach.store_vision_extraction(user.id, extraction)
                    replies.append(response.message)
                finally:
                    incoming_path.unlink(missing_ok=True)
            if replies:
                await message.reply("\n".join(replies))
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


def _is_image_attachment(attachment: discord.Attachment) -> bool:
    content_type = attachment.content_type or ""
    suffix = Path(attachment.filename).suffix.lower()
    return content_type.startswith("image/") or suffix in {".jpg", ".jpeg", ".png", ".webp"}


def _infer_image_kind(message_content: str, filename: str) -> str:
    text = f"{message_content} {filename}".lower()
    if "sleep" in text:
        return ImageKind.SLEEP_SCREENSHOT
    if "progress" in text or "photo" in text:
        return ImageKind.PROGRESS_PHOTO
    if "nutrition" in text or "myfitnesspal" in text or "macro" in text:
        return ImageKind.NUTRITION_SCREENSHOT
    if "cardio" in text or "treadmill" in text or "garmin" in text or "run" in text:
        return ImageKind.CARDIO_SCREENSHOT
    return ImageKind.WORKOUT_SCREENSHOT
