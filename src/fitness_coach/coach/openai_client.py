"""OpenAI Responses API wrapper."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from openai import OpenAI


@dataclass(slots=True)
class OpenAIResult:
    """Text result plus optional structured metadata."""

    text: str
    metadata: dict[str, object]


class CoachOpenAIClient:
    """Small wrapper to keep OpenAI calls replaceable in tests."""

    def __init__(self, api_key: str | None, model: str) -> None:
        self.model = model
        self.client = OpenAI(api_key=api_key) if api_key else None

    def respond(self, *, system_prompt: str, user_message: str) -> OpenAIResult:
        """Generate a coach response from a system prompt and user message."""

        if self.client is None:
            return OpenAIResult(
                text="I’m not connected to OpenAI yet, but I logged the structured update.",
                metadata={"offline": True},
            )

        response = self.client.responses.create(
            model=self.model,
            instructions=system_prompt,
            input=user_message,
        )
        return OpenAIResult(text=response.output_text, metadata={"model": self.model})

    def analyze_text(self, *, system_prompt: str, task: str, text: str) -> OpenAIResult:
        """Analyze user-typed text (not an image) and return structured extraction text."""

        if self.client is None:
            return OpenAIResult(
                text='{"confidence": 0.0, "needs_clarification": true, "facts": {}}',
                metadata={"offline": True},
            )

        response = self.client.responses.create(
            model=self.model,
            instructions=system_prompt,
            input=f"{task}\n\n{text}",
        )
        return OpenAIResult(text=response.output_text, metadata={"model": self.model})

    def analyze_image(self, *, system_prompt: str, image_path: Path, task: str) -> OpenAIResult:
        """Analyze an uploaded image and return structured extraction text."""

        return self.analyze_images(system_prompt=system_prompt, image_paths=[image_path], task=task)

    def analyze_images(
        self, *, system_prompt: str, image_paths: list[Path], task: str
    ) -> OpenAIResult:
        """Analyze one or more related images together and return one extraction."""

        if self.client is None:
            return OpenAIResult(
                text='{"confidence": 0.0, "needs_clarification": true, "facts": {}}',
                metadata={"offline": True, "image_paths": [str(path) for path in image_paths]},
            )

        content: list[dict[str, object]] = [{"type": "input_text", "text": task}]
        for image_path in image_paths:
            uploaded = self.client.files.create(file=image_path.open("rb"), purpose="vision")
            content.append({"type": "input_image", "file_id": uploaded.id})

        response = self.client.responses.create(
            model=self.model,
            instructions=system_prompt,
            input=[{"role": "user", "content": content}],
        )
        return OpenAIResult(text=response.output_text, metadata={"model": self.model})
