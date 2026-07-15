"""Structured memory management."""

from __future__ import annotations

from typing import Any

from fitness_coach.database.repositories import (
    CoachNoteRepository,
    CommitmentEventRepository,
    ConversationMemoryRepository,
    InjuryHistoryRepository,
)
from fitness_coach.database.schemas import MemoryFact


class MemoryService:
    """Maintains summarized durable memory instead of raw Discord history."""

    def __init__(
        self,
        memory_repo: ConversationMemoryRepository,
        commitments: CommitmentEventRepository,
        injuries: InjuryHistoryRepository,
        coach_notes: CoachNoteRepository,
    ) -> None:
        self.memory_repo = memory_repo
        self.commitments = commitments
        self.injuries = injuries
        self.coach_notes = coach_notes

    def upsert_fact(self, user_id: str, fact: MemoryFact) -> None:
        """Create or update a summarized memory fact."""

        self.memory_repo.upsert_fact(
            user_id=user_id,
            key=fact.key,
            value=fact.value,
            confidence=fact.confidence,
            source=fact.source,
        )

    def build_context(self, user_id: str) -> dict[str, Any]:
        """Return structured context safe for prompt injection."""

        facts = self.memory_repo.all_for_user(user_id)
        commitments = self.commitments.open_for_user(user_id)
        injuries = self.injuries.active_for_user(user_id)
        notes = self.coach_notes.active_for_user(user_id)

        return {
            "memory_facts": {fact.key: fact.value for fact in facts},
            "open_commitments": [
                {
                    "id": commitment.id,
                    "description": commitment.description,
                    "due_at": commitment.due_at.isoformat() if commitment.due_at else None,
                    "status": commitment.status.value,
                }
                for commitment in commitments
            ],
            "active_injuries": [
                {
                    "body_area": injury.body_area,
                    "description": injury.description,
                    "status": injury.status.value,
                    "severity": injury.severity,
                }
                for injury in injuries
            ],
            "coach_notes": [
                {
                    "category": note.category,
                    "note": note.note,
                }
                for note in notes
            ],
        }
