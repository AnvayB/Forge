from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import fitness_coach.api.app as api_module
from fitness_coach.api.app import create_app
from fitness_coach.coach.factory import ServiceFactory
from fitness_coach.config.settings import AppSettings, CoachSettings
from fitness_coach.database import models
from fitness_coach.database.repositories import WorkoutEventRepository
from fitness_coach.database.schemas import WorkoutLog


def test_app_construction_defers_database_creation(tmp_path: Path, monkeypatch) -> None:
    def fail_if_initialized(*_args, **_kwargs):
        raise AssertionError("the database factory should be created only on request")

    monkeypatch.setattr(api_module, "ServiceFactory", fail_if_initialized)
    database_path = tmp_path / "not-created-until-request.db"

    app = create_app(
        app_settings=AppSettings(
            database_url=f"sqlite:///{database_path}", config_dir=Path("config")
        ),
        coach_settings=CoachSettings(),
    )

    assert app.title == "Fitness Accountability Coach"
    assert database_path.exists() is False


def test_api_smoke_health_and_workout_event(tmp_path: Path) -> None:
    factory = ServiceFactory(
        AppSettings(
            database_url=f"sqlite:///{tmp_path / 'api.db'}",
            config_dir=Path("config"),
            uploads_dir=tmp_path / "uploads",
        ),
        CoachSettings(preferred_model="test-model"),
    )
    app = create_app(factory)

    health_endpoint = next(route.endpoint for route in app.routes if route.path == "/health")
    workout_endpoint = next(
        route.endpoint for route in app.routes if route.path == "/events/workout"
    )
    with factory.session() as session:
        health = health_endpoint(session)
        response = workout_endpoint(
            WorkoutLog(
                occurred_at=datetime(2026, 7, 1, tzinfo=UTC),
                workout_type="Upper",
                exercises=[
                    {"name": "Bench Press", "sets": [{"weight": 135, "reps": 8}]}
                ],
            ),
            session,
        )

    assert health["database"] == "ok"
    assert response["metadata"]["event_type"] == "workout_completed"
    with factory.session() as session:
        user = session.query(models.User).one()
        events = WorkoutEventRepository(session).recent(user.id)
    assert len(events) == 1
    assert events[0].exercises[0]["name"] == "Bench Press"
    factory.engine.dispose()
