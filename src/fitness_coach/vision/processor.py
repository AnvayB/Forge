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

    def process_workout_screenshots(
        self, *, user_id: str, source_paths: list[Path], extra_notes: str = ""
    ) -> VisionExtraction:
        """Process related workout screenshots (e.g. Arrow + Apple Fitness) as one workout."""

        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp_paths = [self._copy_to_temp(path) for path in source_paths]
        try:
            for tmp_path in tmp_paths:
                self._validate_image(tmp_path)
            extraction = self._analyze(
                user_id=user_id,
                image_paths=tmp_paths,
                kind=ImageKind.WORKOUT_SCREENSHOT,
                extra_notes=extra_notes,
            )
            return VisionExtraction(
                kind=ImageKind.WORKOUT_SCREENSHOT,
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
            "`reps`) into `facts`, using only what the user actually stated."
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
        if kind == ImageKind.WORKOUT_SCREENSHOT:
            task += (
                " These may include an Arrow strength-training screenshot (exercises, sets, "
                "reps, weight) and/or an Apple Fitness summary (workout type, duration, "
                "calories burned, heart rate). Treat all attached images as describing the "
                "same single workout session and merge them into one set of facts: "
                "`workout_type`, `duration_minutes`, `calories_burned`, and `exercises` "
                "(a list of objects with `name` and `sets`, where `sets` is a list of "
                "objects with `weight` and `reps` since weight/reps can vary per set). "
                "Only include fields you can actually read from an image."
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
