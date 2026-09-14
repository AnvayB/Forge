"""OpenAI Responses API wrapper."""

from __future__ import annotations

import logging
import mimetypes
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openai import OpenAI

logger = logging.getLogger(__name__)

ToolExecutor = Callable[[str, str], str]


@dataclass(slots=True)
class OpenAIResult:
    """Text result plus optional structured metadata."""

    text: str
    metadata: dict[str, object] = field(default_factory=dict)


class CoachOpenAIClient:
    """Small wrapper to keep OpenAI calls replaceable in tests."""

    def __init__(self, api_key: str | None, model: str) -> None:
        self.model = model
        self.client = OpenAI(api_key=api_key) if api_key else None

    def respond(
        self,
        *,
        system_prompt: str,
        user_message: str,
        tools: list[dict[str, Any]] | None = None,
        tool_executor: ToolExecutor | None = None,
        max_tool_rounds: int = 4,
    ) -> OpenAIResult:
        """Generate a coach response, running a bounded function-calling loop if tools are given.

        Each round sends every pending `function_call` output back as a
        `function_call_output`, chained on `previous_response_id`. The round cap keeps
        latency predictable; if the model still wants tools after the cap, one final call
        with `tool_choice="none"` forces a text answer from what it already has.
        """

        if self.client is None:
            return OpenAIResult(
                text="I’m not connected to OpenAI yet, but I logged the structured update.",
                metadata={"offline": True},
            )

        extra: dict[str, Any] = {"tools": tools} if tools else {}
        response = self.client.responses.create(
            model=self.model,
            instructions=system_prompt,
            input=user_message,
            **extra,
        )

        tool_calls: list[dict[str, str]] = []
        rounds = 0
        while tools and tool_executor is not None:
            pending = [
                item
                for item in (response.output or [])
                if getattr(item, "type", None) == "function_call"
            ]
            if not pending:
                break
            if rounds >= max_tool_rounds:
                logger.warning("tool_round_cap_reached rounds=%s", rounds)
                response = self.client.responses.create(
                    model=self.model,
                    instructions=system_prompt,
                    input=[
                        {
                            "type": "function_call_output",
                            "call_id": call.call_id,
                            "output": (
                                '{"error": "tool budget exhausted; answer with what you have"}'
                            ),
                        }
                        for call in pending
                    ],
                    previous_response_id=response.id,
                    tools=tools,
                    tool_choice="none",
                )
                break
            outputs: list[dict[str, str]] = []
            for call in pending:
                output = tool_executor(call.name, call.arguments)
                tool_calls.append({"name": call.name, "arguments": call.arguments})
                outputs.append(
                    {"type": "function_call_output", "call_id": call.call_id, "output": output}
                )
            rounds += 1
            response = self.client.responses.create(
                model=self.model,
                instructions=system_prompt,
                input=outputs,
                previous_response_id=response.id,
                tools=tools,
            )

        return OpenAIResult(
            text=response.output_text,
            metadata={"model": self.model, "tool_calls": tool_calls, "tool_rounds": rounds},
        )

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
            # OpenAI's upload validation checks the filename extension case-sensitively
            # (e.g. iPhone screenshots named "IMG_1234.PNG" are rejected), so force lowercase.
            lowercase_name = image_path.name.lower()
            content_type = mimetypes.guess_type(lowercase_name)[0] or "application/octet-stream"
            uploaded = self.client.files.create(
                file=(lowercase_name, image_path.open("rb"), content_type),
                purpose="vision",
            )
            content.append({"type": "input_image", "file_id": uploaded.id})

        response = self.client.responses.create(
            model=self.model,
            instructions=system_prompt,
            input=[{"role": "user", "content": content}],
        )
        return OpenAIResult(text=response.output_text, metadata={"model": self.model})
