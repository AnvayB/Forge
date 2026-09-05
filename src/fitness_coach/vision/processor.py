"""Temporary image proof processing and retention."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PIL import Image

from fitness_coach.coach.openai_client import CoachOpenAIClient, OpenAIResult
from fitness_coach.config.prompt_builder import PromptBuilder
from fitness_coach.config.settings import CoachSettings


class ImageKind:
    """Supported extraction source categories."""

    WORKOUT_SCREENSHOT = "workout_screenshot"
    WORKOUT_TEXT = "workout_text"
    CARDIO_SCREENSHOT = "cardio_screenshot"
    NUTRITION_SCREENSHOT = "nutrition_screenshot"
    SLEEP_SCREENSHOT = "sleep_screenshot"
    PROGRESS_PHOTO = "progress_photo"


_VALID_IMAGE_KINDS = {
    ImageKind.WORKOUT_SCREENSHOT,
    ImageKind.CARDIO_SCREENSHOT,
    ImageKind.NUTRITION_SCREENSHOT,
    ImageKind.SLEEP_SCREENSHOT,
    ImageKind.PROGRESS_PHOTO,
}

_AUTO_DETECT_TASK = (
    "Identify what kind of fitness/nutrition screenshot this is, then extract structured "
    "facts from it. Return JSON with keys `kind`, `confidence`, `needs_clarification`, and "
    "`facts`. `kind` must be exactly one of: `workout_screenshot`, `cardio_screenshot`, "
    "`nutrition_screenshot`, `sleep_screenshot`, `progress_photo`. Field conventions per "
    "kind (only include fields you can actually read):\n"
    "- workout_screenshot (e.g. Arrow, Apple Fitness strength training): `workout_type`, "
    "`duration_minutes`, `calories_burned`, `exercises` (a list of objects with `name` and "
    "`sets`, where `sets` is a list of objects with `weight` and `reps` as plain numbers, "
    "no unit text).\n"
    "- cardio_screenshot (e.g. treadmill, Garmin run): `modality`, `duration_minutes`, "
    "`distance_miles`, `calories_burned`, `average_heart_rate`, `incline`, `speed_mph`.\n"
    "- nutrition_screenshot (e.g. MyFitnessPal 'Nutrients Remaining' widget, which shows "
    "how much of the daily budget is left rather than how much has been consumed - read "
    "the remaining amounts exactly as shown, which can be negative if the goal was "
    "exceeded): `calories_remaining`, `protein_g_remaining`, `carbs_g_remaining`, "
    "`fat_g_remaining`.\n"
    "- sleep_screenshot: `time_asleep_minutes`, `bedtime`, `wake_time`, "
    "`time_awake_minutes`, `light_minutes`, `deep_minutes`, `rem_minutes`, "
    "`regularity_percent`, `sleep_latency_minutes`, `wake_up_mood`.\n"
    "- progress_photo: a physique/body photo. Put a short, honest, constructive "
    "observation about visible physique (e.g. midsection/stomach fat, overall build) "
    "into `facts.feedback` - specific and checkable, never vague praise, hype, or "
    "derision. There is no previous photo available here, so treat this as a "
    "standalone baseline; don't invent a comparison. Never state a specific body-fat "
    "percentage, weight, or waist measurement unless a scale or tape measure is "
    "visible and readable in the frame - if one is, also put it in `body_weight_lb`, "
    "`waist_inches`, or `body_fat_percent`. Set `needs_clarification` to false and "
    "confidence to at least 0.8 as long as a person's body is clearly visible.\n"
    "If confidence is low, or you cannot tell what kind this is, set needs_clarification "
    "to true."
)


@dataclass(frozen=True, slots=True)
class VisionExtraction:
    """Structured output from image processing."""

    kind: str
    confidence: float
    needs_clarification: bool
    facts: dict[str, Any]
    retained_path: str | None


class VisionProcessor:
    """Validate, analyze, and clean up uploaded images."""

    def __init__(
        self,
        *,
        uploads_dir: Path,
        settings: CoachSettings,
        prompt_builder: PromptBuilder,
        openai_client: CoachOpenAIClient,
    ) -> None:
        self.uploads_dir = uploads_dir
        self.settings = settings
        self.prompt_builder = prompt_builder
        self.openai = openai_client
        self.tmp_dir = uploads_dir / "tmp"
        self.progress_dir = uploads_dir / "progress"

    def process(self, *, user_id: str, source_path: Path, kind: str) -> VisionExtraction:
        """Process an uploaded image and apply retention policy."""

        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = self._copy_to_temp(source_path)
        retained_path: Path | None = None
        try:
            self._validate_image(tmp_path)
            if kind == ImageKind.PROGRESS_PHOTO and self.settings.retain_progress_photos:
                retained_path = self._retain_progress_photo(tmp_path)

            extraction = self._analyze(user_id=user_id, image_paths=[tmp_path], kind=kind)
            return VisionExtraction(
                kind=kind,
                confidence=extraction.get("confidence", 0.0),
                needs_clarification=extraction.get("needs_clarification", True),
                facts=extraction.get("facts", {}),
                retained_path=str(retained_path) if retained_path else None,
            )
        finally:
            if kind == ImageKind.PROGRESS_PHOTO:
                tmp_path.unlink(missing_ok=True)
            else:
                self._delete_if_configured(tmp_path)

    def process_auto(self, *, user_id: str, source_path: Path) -> VisionExtraction:
        """Process a screenshot whose kind couldn't be inferred from message text.

        Lets the vision model classify it directly instead of assuming a default kind.
        """

        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = self._copy_to_temp(source_path)
        kind = ImageKind.WORKOUT_SCREENSHOT
        retained_path: Path | None = None
        try:
            self._validate_image(tmp_path)
            prompt = self.prompt_builder.build(user_id)
            result = self.openai.analyze_images(
                system_prompt=prompt,
                image_paths=[tmp_path],
                task=_AUTO_DETECT_TASK,
            )
            extraction = self._parse_extraction_result(result)
            candidate = str(extraction.get("kind", "")).strip().lower()
            if candidate in _VALID_IMAGE_KINDS:
                kind = candidate
            if kind == ImageKind.PROGRESS_PHOTO and self.settings.retain_progress_photos:
                retained_path = self._retain_progress_photo(tmp_path)
            return VisionExtraction(
                kind=kind,
                confidence=extraction.get("confidence", 0.0),
                needs_clarification=extraction.get("needs_clarification", True),
                facts=extraction.get("facts", {}),
                retained_path=str(retained_path) if retained_path else None,
            )
        finally:
            if kind == ImageKind.PROGRESS_PHOTO:
                tmp_path.unlink(missing_ok=True)
            else:
                self._delete_if_configured(tmp_path)

    def process_progress_photo(
        self,
        *,
        user_id: str,
        source_path: Path,
        previous_photo_path: str | None = None,
        previous_measured_at: datetime | None = None,
    ) -> VisionExtraction:
        """Analyze a progress photo, comparing it against the previous one when available.

        Bypasses `_analyze()` - its per-kind blocks only append field guidance onto one
        shared "extract structured facts" preamble, with no clean seam for dual-image
        ordering and date-substitution (same precedent as `process_workout_text`,
        which also builds its own task text directly).
        """

        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = self._copy_to_temp(source_path)
        retained_path: Path | None = None
        try:
            self._validate_image(tmp_path)
            if self.settings.retain_progress_photos:
                retained_path = self._retain_progress_photo(tmp_path)

            previous = Path(previous_photo_path) if previous_photo_path else None
            if previous is not None and previous_measured_at is not None and previous.exists():
                image_paths = [previous, tmp_path]
                task = self._progress_photo_comparison_task(previous_measured_at)
            else:
                image_paths = [tmp_path]
                task = self._progress_photo_baseline_task()

            prompt = self.prompt_builder.build(user_id)
            result = self.openai.analyze_images(
                system_prompt=prompt,
                image_paths=image_paths,
                task=task,
            )
            extraction = self._parse_extraction_result(result)
            return VisionExtraction(
                kind=ImageKind.PROGRESS_PHOTO,
                confidence=extraction.get("confidence", 0.0),
                needs_clarification=extraction.get("needs_clarification", True),
                facts=extraction.get("facts", {}),
                retained_path=str(retained_path) if retained_path else None,
            )
        finally:
            tmp_path.unlink(missing_ok=True)

    def _progress_photo_comparison_task(self, previous_measured_at: datetime) -> str:
        day_gap = (datetime.now(UTC).date() - previous_measured_at.date()).days
        return (
            "The user sent a new progress/physique photo. You are also given their most "
            "recent previous progress photo for comparison: the FIRST attached image is "
            f"the previous photo (taken {previous_measured_at.date().isoformat()}), and "
            "the SECOND is the new one (taken today, "
            f"{datetime.now(UTC).date().isoformat()}) - {day_gap} day(s) apart. Return "
            "JSON with keys `confidence`, `needs_clarification`, and `facts`.\n\n"
            "Compare the two photos and write a short, honest, constructive comment into "
            "`facts.feedback` about visible physique changes (e.g. midsection/stomach "
            "fat, shoulders/chest/arms, overall build and posture) - or genuine lack of "
            "visible change, if that's what you actually see. Rules:\n"
            "- Never state a specific body-fat percentage, weight, or waist measurement "
            "unless a scale or tape measure is visible and readable in the frame; if one "
            "is, also put the reading in `body_weight_lb`, `waist_inches`, or "
            "`body_fat_percent`.\n"
            "- Be specific and checkable (\"midsection looks slightly leaner through the "
            "waist\" not \"looking great!\"). No vague praise, no hype, no derision - be "
            "direct and honest, the way a coach who respects the person tells them the "
            "truth.\n"
            "- If lighting, angle, distance, or pose differ enough that a fair visual "
            "comparison isn't really possible, say so plainly instead of guessing, and "
            "describe what you can still tell from the new photo alone.\n"
            f"- Mention the {day_gap}-day gap when it's relevant to interpreting what you "
            "see (a long gap with little visible change is itself worth naming "
            "honestly).\n"
            "- If you note something moving in an unwanted direction, pair it with one "
            "concrete, actionable next step - never leave a criticism without a path "
            "forward.\n"
            "- Set `needs_clarification` to false and `confidence` to at least 0.8 as "
            "long as you can clearly see a person's body in both photos, even if you end "
            "up declining or heavily qualifying the comparison - a qualified or declined "
            "comparison is still a complete, useful answer. Only set "
            "`needs_clarification` to true if the image(s) don't actually show a person "
            "clearly enough to say anything (too dark, too cropped, or not a physique "
            "photo at all)."
        )

    def _progress_photo_baseline_task(self) -> str:
        return (
            "The user sent a progress/physique photo. No previous progress photo is "
            "available to compare it against yet. Return JSON with keys `confidence`, "
            "`needs_clarification`, and `facts`.\n\n"
            "Write a short, honest, constructive observation into `facts.feedback` about "
            "what's visible (e.g. midsection/stomach fat, shoulders/chest/arms, overall "
            "build and posture), framed as a baseline - do not invent a comparison that "
            "doesn't exist. Rules:\n"
            "- Never state a specific body-fat percentage, weight, or waist measurement "
            "unless a scale or tape measure is visible and readable in the frame; if one "
            "is, also put the reading in `body_weight_lb`, `waist_inches`, or "
            "`body_fat_percent`.\n"
            "- Be specific and checkable, not vague praise or hype, and never derisive.\n"
            "- Mention this is a starting point for future comparison, not a verdict.\n"
            "- Set `needs_clarification` to false and `confidence` to at least 0.8 as "
            "long as you can clearly see a person's body in the photo. Only set "
            "`needs_clarification` to true if the image doesn't actually show a person "
            "clearly enough to say anything."
        )

    def process_workout_screenshots(
        self, *, user_id: str, source_paths: list[Path], extra_notes: str = ""
    ) -> VisionExtraction:
        """Process related workout screenshots (e.g. Arrow + Apple Fitness) as one workout."""

        return self._process_screenshots(
            user_id=user_id,
            source_paths=source_paths,
            kind=ImageKind.WORKOUT_SCREENSHOT,
            extra_notes=extra_notes,
        )

    def process_cardio_screenshots(
        self, *, user_id: str, source_paths: list[Path], extra_notes: str = ""
    ) -> VisionExtraction:
        """Process one or more cardio screenshots (e.g. Apple Fitness, Garmin) as one session."""

        return self._process_screenshots(
            user_id=user_id,
            source_paths=source_paths,
            kind=ImageKind.CARDIO_SCREENSHOT,
            extra_notes=extra_notes,
        )

    def _process_screenshots(
        self, *, user_id: str, source_paths: list[Path], kind: str, extra_notes: str = ""
    ) -> VisionExtraction:
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp_paths = [self._copy_to_temp(path) for path in source_paths]
        try:
            for tmp_path in tmp_paths:
                self._validate_image(tmp_path)
            extraction = self._analyze(
                user_id=user_id,
                image_paths=tmp_paths,
                kind=kind,
                extra_notes=extra_notes,
            )
            return VisionExtraction(
                kind=kind,
                confidence=extraction.get("confidence", 0.0),
                needs_clarification=extraction.get("needs_clarification", True),
                facts=extraction.get("facts", {}),
                retained_path=None,
            )
        finally:
            for tmp_path in tmp_paths:
                self._delete_if_configured(tmp_path)

    def process_workout_text(self, *, user_id: str, text: str) -> VisionExtraction:
        """Parse a typed workout description into the same structured facts as a screenshot."""

        prompt = self.prompt_builder.build(user_id)
        task = (
            "The user typed this workout description themselves (it is not a screenshot), "
            "so treat it as ground truth and extract structured facts confidently unless "
            "it is genuinely too vague to log. Return JSON with keys `confidence`, "
            "`needs_clarification`, and `facts`. Put `workout_type`, `duration_minutes`, "
            "`calories_burned` (only if mentioned), and `exercises` (a list of objects "
            "with `name` and `sets`, where `sets` is a list of objects with `weight` and "
            "`reps`) into `facts`, using only what the user actually stated. `weight` and "
            "`reps` must be plain numbers with no unit text (30, not '30 lbs' or '30lbs')."
        )
        result = self.openai.analyze_text(system_prompt=prompt, task=task, text=text)
        extraction = self._parse_extraction_result(result)
        return VisionExtraction(
            kind=ImageKind.WORKOUT_TEXT,
            confidence=extraction.get("confidence", 0.0),
            needs_clarification=extraction.get("needs_clarification", True),
            facts=extraction.get("facts", {}),
            retained_path=None,
        )

    def _copy_to_temp(self, source_path: Path) -> Path:
        destination = self.tmp_dir / f"{datetime.now(UTC).timestamp()}_{source_path.name}"
        shutil.copy2(source_path, destination)
        return destination

    def _validate_image(self, image_path: Path) -> None:
        with Image.open(image_path) as image:
            image.verify()

    def _retain_progress_photo(self, image_path: Path) -> Path:
        dated_dir = self.progress_dir / datetime.now(UTC).date().isoformat()
        dated_dir.mkdir(parents=True, exist_ok=True)
        retained_path = dated_dir / image_path.name
        shutil.copy2(image_path, retained_path)
        return retained_path

    def _analyze(
        self,
        *,
        user_id: str,
        image_paths: list[Path],
        kind: str,
        extra_notes: str = "",
    ) -> dict[str, Any]:
        prompt = self.prompt_builder.build(user_id)
        image_word = "image" if len(image_paths) == 1 else "images"
        task = (
            f"Extract structured fitness facts from the attached {image_word}. Return JSON "
            "with keys `confidence`, `needs_clarification`, and `facts`. The image kind is "
            f"{kind}. If confidence is low, set needs_clarification to true."
        )
        if kind == ImageKind.NUTRITION_SCREENSHOT:
            task += (
                " This is typically a MyFitnessPal 'Nutrients Remaining' widget, which shows "
                "how much of the daily budget is left rather than how much has been consumed. "
                "Read the remaining amounts exactly as shown (a value can be negative if the "
                "daily goal was already exceeded) and put them in `facts` as "
                "`calories_remaining`, `protein_g_remaining`, `carbs_g_remaining`, and "
                "`fat_g_remaining`."
            )
        if kind == ImageKind.CARDIO_SCREENSHOT:
            task += (
                " Put `modality` (e.g. Indoor Run, Outdoor Bike), `duration_minutes`, "
                "`distance_miles`, `calories_burned`, `average_heart_rate`, `incline`, and "
                "`speed_mph` into `facts` (only fields you can actually read). If the "
                "screenshot shows both Active and Total calories (e.g. Apple Fitness), use "
                "Active Calories for `calories_burned`, since that is the exercise itself "
                "rather than the full-day total."
            )
        if kind == ImageKind.WORKOUT_SCREENSHOT:
            task += (
                " These may include an Arrow strength-training screenshot (exercises, sets, "
                "reps, weight) and/or an Apple Fitness summary (workout type, duration, "
                "calories burned, heart rate). Treat all attached images as describing the "
                "same single workout session and merge them into one set of facts: "
                "`workout_type`, `duration_minutes`, `calories_burned`, and `exercises` "
                "(a list of objects with `name` and `sets`, where `sets` is a list of "
                "objects with `weight` and `reps` since weight/reps can vary per set). "
                "`weight` and `reps` must be plain numbers with no unit text (30, not "
                "'30 lbs' or '30lbs'). Only include fields you can actually read from an "
                "image."
            )
        if extra_notes:
            task += f" The user also provided this note: {extra_notes!r}."
        result = self.openai.analyze_images(
            system_prompt=prompt,
            image_paths=image_paths,
            task=task,
        )
        return self._parse_extraction_result(result)

    def _parse_extraction_result(self, result: OpenAIResult) -> dict[str, Any]:
        try:
            parsed = json.loads(result.text)
        except json.JSONDecodeError:
            return {
                "confidence": 0.0,
                "needs_clarification": True,
                "facts": {"raw_response": result.text},
            }
        if not isinstance(parsed, dict):
            return {"confidence": 0.0, "needs_clarification": True, "facts": {}}
        return parsed

    def _delete_if_configured(self, image_path: Path) -> None:
        if self.settings.retain_processed_screenshots:
            return
        image_path.unlink(missing_ok=True)
