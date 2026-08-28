"""Structured memory management."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from fitness_coach.database.repositories import (
    CoachNoteRepository,
    CommitmentEventRepository,
    ConversationMemoryRepository,
    ExerciseBaselineRepository,
    InjuryHistoryRepository,
    PlanOverrideRepository,
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
        plan_overrides: PlanOverrideRepository,
        exercise_baselines: ExerciseBaselineRepository,
        timezone: str,
    ) -> None:
        self.memory_repo = memory_repo
        self.commitments = commitments
        self.injuries = injuries
        self.coach_notes = coach_notes
        self.plan_overrides = plan_overrides
        self.exercise_baselines = exercise_baselines
        self.timezone = timezone

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
        today = datetime.now(ZoneInfo(self.timezone)).date()
        overrides = self.plan_overrides.active_for_user(user_id, today)
        baselines = self.exercise_baselines.list_for_user(user_id)

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
            "active_plan_overrides": [
                {
                    "description": override.description,
                    "starts_on": override.starts_on.isoformat(),
                    "expires_on": override.expires_on.isoformat(),
                }
                for override in overrides
            ],
            "exercise_baselines": [
                {
                    "exercise": baseline.display_name,
                    "baseline_weight": baseline.baseline_weight,
                    "max_weight": baseline.max_weight,
                    "consecutive_sessions_at_current_weight": (
                        baseline.consecutive_sessions_at_tracked_weight
                    ),
                }
                for baseline in baselines
            ],
        }
