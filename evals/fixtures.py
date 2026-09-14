"""Deterministic user history seeded before every eval run.

Scenario expectations (e.g. "must mention 185") refer to these facts, so change them
together. Everything is dated relative to `now` so the tool windows behave the same
whenever the evals run.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fitness_coach.coach.service import CoachService
from fitness_coach.database import models
from fitness_coach.database.repositories import (
    ConversationMemoryRepository,
    InjuryHistoryRepository,
)
from fitness_coach.database.schemas import (
    CardioLog,
    CommitmentCreate,
    NutritionLog,
    SleepLog,
    WorkoutLog,
)

FIXTURE_FACTS = {
    "bench_stall_weight": 185,
    "bench_stall_reps": 5,
    "bench_stall_sessions": 4,
    "incline_db_progression": [60, 65, 70],
    "shoulder_injury_note": "irritable on flat barbell pressing",
    "commitment": "Do cardio Friday morning before work",
    "preferred_training_time": "evenings",
}


def seed_history(
    coach: CoachService, session, user_id: str, *, now: datetime | None = None
) -> None:
    now = now or datetime.now(UTC)

    # Bench: one improvement five weeks ago, then four flat sessions -> stalled.
    for weeks_ago, weight in ((5, 175), (4, 185), (3, 185), (2, 185), (1, 185)):
        coach.log_workout(
            user_id,
            WorkoutLog(
                occurred_at=now - timedelta(weeks=weeks_ago, days=1),
                workout_type="Upper",
                exercises=[
                    {"name": "Flat Bench Press", "sets": [{"weight": weight, "reps": 5}] * 3},
                    {"name": "Lat Pulldown", "sets": [{"weight": 120, "reps": 10}] * 3},
                ],
            ),
        )
    # Incline dumbbell press progressing.
    for weeks_ago, weight in ((3, 60), (2, 65), (1, 70)):
        coach.log_workout(
            user_id,
            WorkoutLog(
                occurred_at=now - timedelta(weeks=weeks_ago, days=3),
                workout_type="Chest + Back + Shoulders",
                exercises=[
                    {"name": "Incline Dumbbell Press", "sets": [{"weight": weight, "reps": 8}] * 3},
                    {"name": "Shoulder Press", "sets": [{"weight": 40, "reps": 10}] * 3},
                ],
            ),
        )
    # Legs: leg press climbing.
    for weeks_ago, weight in ((2, 270), (1, 290)):
        coach.log_workout(
            user_id,
            WorkoutLog(
                occurred_at=now - timedelta(weeks=weeks_ago, days=2),
                workout_type="Legs",
                exercises=[
                    {"name": "Leg Press", "sets": [{"weight": weight, "reps": 10}] * 3},
                    {"name": "Seated Hamstring Curl", "sets": [{"weight": 90, "reps": 12}] * 3},
                ],
            ),
        )
    # Cardio three times in the last week.
    for days_ago in (2, 4, 6):
        coach.log_cardio(
            user_id,
            CardioLog(
                occurred_at=now - timedelta(days=days_ago),
                modality="Incline Walk",
                duration_minutes=35,
                incline=10,
                speed_mph=3.2,
            ),
        )
    # Nutrition and sleep for the last week.
    for days_ago in range(1, 8):
        coach.log_nutrition(
            user_id,
            NutritionLog(
                logged_for=now - timedelta(days=days_ago),
                calories=2300 + (days_ago % 3) * 40,
                protein_g=150 + (days_ago % 4) * 5,
                carbs_g=290,
                fat_g=60,
            ),
        )
        coach.log_sleep(
            user_id,
            SleepLog(
                logged_for=now - timedelta(days=days_ago),
                time_asleep_minutes=380 + (days_ago % 3) * 20,
                regularity_percent=85,
                wake_up_mood="OK",
            ),
        )
    coach.set_exercise_baseline(user_id, "Flat Bench Press", 185, 205)
    coach.create_commitment(user_id, CommitmentCreate(description=FIXTURE_FACTS["commitment"]))
    InjuryHistoryRepository(session).add(
        models.InjuryHistory(
            user_id=user_id,
            body_area="shoulder",
            description=FIXTURE_FACTS["shoulder_injury_note"],
            status=models.InjuryStatus.MONITORING,
            severity=2,
        )
    )
    ConversationMemoryRepository(session).upsert_fact(
        user_id=user_id,
        key="preferred_training_time",
        value={"value": FIXTURE_FACTS["preferred_training_time"]},
    )
    session.flush()
