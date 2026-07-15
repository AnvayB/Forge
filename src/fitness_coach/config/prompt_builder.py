"""Prompt assembly for all LLM interactions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol


class ContextProvider(Protocol):
    """Protocol for services that provide SQLite-derived prompt context."""

    def build_context(self, user_id: str) -> dict[str, Any]:
        """Build structured context for a user."""


class PromptBuilder:
    """Assemble the final system prompt in the required project order."""

    REQUIRED_FILES = (
        "system_prompt.md",
        "coach_principles.md",
        "user_profile.md",
        "training_preferences.md",
    )

    def __init__(self, config_dir: Path, context_provider: ContextProvider | None = None) -> None:
        self.config_dir = config_dir
        self.context_provider = context_provider

    def build(self, user_id: str) -> str:
        """Build the complete system prompt for a user."""

        sections = [self._load_prompt_file(file_name) for file_name in self.REQUIRED_FILES]
        context = self.context_provider.build_context(user_id) if self.context_provider else {}
        sections.append(self._format_dynamic_context(context))
        return "\n\n---\n\n".join(sections)

    def _load_prompt_file(self, file_name: str) -> str:
        path = self.config_dir / file_name
        if not path.exists():
            raise FileNotFoundError(f"Missing required prompt file: {path}")
        return path.read_text(encoding="utf-8").strip()

    def _format_dynamic_context(self, context: dict[str, Any]) -> str:
        return (
            "# Dynamic SQLite Context\n\n"
            "The following context is structured data from SQLite. Treat it as memory, "
            "but do not reveal locked analytics unless policy allows it.\n\n"
            f"```json\n{json.dumps(context, indent=2, sort_keys=True)}\n```"
        )
