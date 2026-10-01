"""FastAPI app for health checks and local diagnostics."""

from __future__ import annotations

from datetime import UTC, datetime

import uvicorn
from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.orm import Session

from fitness_coach.coach.factory import ServiceFactory
from fitness_coach.config.settings import (
    AppSettings,
    CoachSettings,
    get_app_settings,
    get_coach_settings,
)
from fitness_coach.database.schemas import CardioLog, NutritionLog, WorkoutLog
from fitness_coach.logging import configure_logging


def create_app(
    factory: ServiceFactory | None = None,
    *,
    app_settings: AppSettings | None = None,
    coach_settings: CoachSettings | None = None,
) -> FastAPI:
    """Create the FastAPI app, with settings and services injectable for tests."""

    app_settings = app_settings or (factory.app_settings if factory else get_app_settings())
    coach_settings = coach_settings or (
        factory.coach_settings if factory else get_coach_settings()
    )
    configure_logging(app_settings.log_level)
    service_factory = factory

    def get_factory() -> ServiceFactory:
        nonlocal service_factory
        if service_factory is None:
            service_factory = ServiceFactory(app_settings, coach_settings)
        return service_factory

    app = FastAPI(title="Fitness Accountability Coach")

    def get_session() -> Session:
        with get_factory().session() as session:
            yield session

    @app.get("/health")
    def health(session: Session = Depends(get_session)) -> dict[str, object]:  # noqa: B008
        session.execute(text("select 1"))
        return {
            "status": "ok",
            "time": datetime.now(UTC).isoformat(),
            "database": "ok",
            "analytics_locked": coach_settings.analytics_locked,
        }

    @app.post("/events/workout")
    def log_workout(
        payload: WorkoutLog,
        session: Session = Depends(get_session),  # noqa: B008
    ) -> dict[str, object]:
        coach = get_factory().coach_service(session)
        user = coach.get_user()
        return coach.log_workout(user.id, payload).model_dump()

    @app.post("/events/cardio")
    def log_cardio(
        payload: CardioLog,
        session: Session = Depends(get_session),  # noqa: B008
    ) -> dict[str, object]:
        coach = get_factory().coach_service(session)
        user = coach.get_user()
        return coach.log_cardio(user.id, payload).model_dump()

    @app.post("/events/nutrition")
    def log_nutrition(
        payload: NutritionLog,
        session: Session = Depends(get_session),  # noqa: B008
    ) -> dict[str, object]:
        coach = get_factory().coach_service(session)
        user = coach.get_user()
        return coach.log_nutrition(user.id, payload).model_dump()

    return app


app = create_app()


def run() -> None:
    """Run the API with uvicorn."""

    uvicorn.run("fitness_coach.api.app:app", host="127.0.0.1", port=8000, reload=False)
