"""Application service factory."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.orm import Session

from fitness_coach.coach.openai_client import CoachOpenAIClient
from fitness_coach.coach.service import CoachService
from fitness_coach.config.prompt_builder import PromptBuilder
from fitness_coach.config.settings import AppSettings, CoachSettings
from fitness_coach.database.repositories import (
    CardioEventRepository,
    CoachNoteRepository,
    CommitmentEventRepository,
    ConversationMemoryRepository,
    GoalRepository,
    InjuryHistoryRepository,
    MeasurementEventRepository,
    NutritionEventRepository,
    PlanOverrideRepository,
    ProgressReviewRepository,
    SleepEventRepository,
    UserRepository,
    WorkoutEventRepository,
    WorkoutPlanRepository,
)
from fitness_coach.database.session import create_db_engine, create_session_factory, init_db
from fitness_coach.memory.service import MemoryService
from fitness_coach.vision.processor import VisionProcessor


class ServiceFactory:
    """Creates request-scoped services backed by shared engine configuration."""

    def __init__(self, app_settings: AppSettings, coach_settings: CoachSettings) -> None:
        self.app_settings = app_settings
        self.coach_settings = coach_settings
        self.engine = create_db_engine(app_settings.database_url)
        init_db(self.engine)
        self.session_factory = create_session_factory(self.engine)

    @contextmanager
    def session(self) -> Iterator[Session]:
        """Open a transactional session."""

        session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def coach_service(self, session: Session) -> CoachService:
        """Create a coach service for a SQLAlchemy session."""

        memory_service = self.memory_service(session)
        prompt_builder = PromptBuilder(self.app_settings.config_dir, memory_service)
        return CoachService(
            settings=self.coach_settings,
            prompt_builder=prompt_builder,
            openai_client=CoachOpenAIClient(
                api_key=self.app_settings.openai_api_key,
                model=self.coach_settings.preferred_model,
            ),
            users=UserRepository(session),
            workouts=WorkoutEventRepository(session),
            cardio=CardioEventRepository(session),
            nutrition=NutritionEventRepository(session),
            sleep=SleepEventRepository(session),
            measurements=MeasurementEventRepository(session),
            commitments=CommitmentEventRepository(session),
            workout_plans=WorkoutPlanRepository(session),
            progress_reviews=ProgressReviewRepository(session),
            memory=ConversationMemoryRepository(session),
            plan_overrides=PlanOverrideRepository(session),
        )

    def memory_service(self, session: Session) -> MemoryService:
        """Create the structured memory service."""

        return MemoryService(
            memory_repo=ConversationMemoryRepository(session),
            commitments=CommitmentEventRepository(session),
            injuries=InjuryHistoryRepository(session),
            coach_notes=CoachNoteRepository(session),
            plan_overrides=PlanOverrideRepository(session),
            timezone=self.coach_settings.timezone,
        )

    def vision_processor(self, session: Session) -> VisionProcessor:
        """Create the image proof processor."""

        memory_service = self.memory_service(session)
        prompt_builder = PromptBuilder(self.app_settings.config_dir, memory_service)
        return VisionProcessor(
            uploads_dir=self.app_settings.uploads_dir,
            settings=self.coach_settings,
            prompt_builder=prompt_builder,
            openai_client=CoachOpenAIClient(
                api_key=self.app_settings.openai_api_key,
                model=self.coach_settings.preferred_model,
            ),
        )

    def repositories(self, session: Session) -> dict[str, object]:
        """Expose repositories for diagnostics and future admin tasks."""

        return {
            "users": UserRepository(session),
            "workouts": WorkoutEventRepository(session),
            "cardio": CardioEventRepository(session),
            "nutrition": NutritionEventRepository(session),
            "sleep": SleepEventRepository(session),
            "measurements": MeasurementEventRepository(session),
            "commitments": CommitmentEventRepository(session),
            "progress_reviews": ProgressReviewRepository(session),
            "memory": ConversationMemoryRepository(session),
            "plan_overrides": PlanOverrideRepository(session),
            "goals": GoalRepository(session),
            "injuries": InjuryHistoryRepository(session),
            "plans": WorkoutPlanRepository(session),
            "coach_notes": CoachNoteRepository(session),
        }
