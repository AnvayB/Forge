"""Temporary image proof processing and retention."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PIL import Image

from fitness_coach.coach.openai_client import CoachOpenAIClient
from fitness_coach.config.prompt_builder import PromptBuilder
from fitness_coach.config.settings import CoachSettings


class ImageKind:
    """Supported image retention categories."""

    WORKOUT_SCREENSHOT = "workout_screenshot"
    CARDIO_SCREENSHOT = "cardio_screenshot"
    NUTRITION_SCREENSHOT = "nutrition_screenshot"
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

            extraction = self._analyze(user_id=user_id, image_path=tmp_path, kind=kind)
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

    def _analyze(self, *, user_id: str, image_path: Path, kind: str) -> dict[str, Any]:
        prompt = self.prompt_builder.build(user_id)
        result = self.openai.analyze_image(
            system_prompt=prompt,
            image_path=image_path,
            task=(
                "Extract structured fitness facts from this image. Return JSON with keys "
                "`confidence`, `needs_clarification`, and `facts`. The image kind is "
                f"{kind}. If confidence is low, set needs_clarification to true."
            ),
        )
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
