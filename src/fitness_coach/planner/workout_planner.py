"""Deterministic workout planner."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime

from fitness_coach.database import models
from fitness_coach.database.repositories import WorkoutPlanRepository
from fitness_coach.database.schemas import WorkoutPlanRequest


@dataclass(frozen=True, slots=True)
class ExerciseTemplate:
    """Exercise prescription template."""

    name: str
    sets: int
    reps: str
    notes: str = ""


SPLIT_BY_WEEKDAY = {
    6: ("Lower", "Lower body strength"),
    0: ("Rest", "Rest or light cardio"),
    1: ("Chest + Back + Front Delts", "Upper torso strength"),
    2: ("Arms + Side Delts", "Arms and lateral delts"),
    3: ("Legs + Rear Delts", "Legs and posterior delts"),
    4: ("Rest", "Rest or light cardio"),
    5: ("Upper", "Upper body hypertrophy"),
}

EXERCISES = {
    "Lower": [
        ExerciseTemplate("leg press machine", 4, "8-12"),
        ExerciseTemplate("leg curl", 3, "10-15"),
        ExerciseTemplate("leg extension", 3, "10-15"),
        ExerciseTemplate("calf raise", 3, "10-15"),
    ],
    "Chest + Back + Front Delts": [
        ExerciseTemplate("incline bench press", 3, "6-10"),
        ExerciseTemplate("chest supported T-bar row", 4, "8-12"),
        ExerciseTemplate("incline chest press machine", 3, "8-12"),
        ExerciseTemplate("lat pulldown", 3, "8-12"),
        ExerciseTemplate(
            "machine shoulder press",
            2,
            "8-12",
            "Skip if shoulder discomfort appears.",
        ),
    ],
    "Arms + Side Delts": [
        ExerciseTemplate("bicep preacher curls", 3, "8-12"),
        ExerciseTemplate("cable tricep pushdown", 3, "10-15"),
        ExerciseTemplate("tricep extension machine", 3, "10-15"),
        ExerciseTemplate("cable lateral raise", 4, "12-20"),
    ],
    "Legs + Rear Delts": [
        ExerciseTemplate("decline leg press", 4, "8-12"),
        ExerciseTemplate("seated leg curl", 3, "10-15"),
        ExerciseTemplate("leg extension", 3, "10-15"),
        ExerciseTemplate("rear delt fly", 4, "12-20"),
    ],
    "Upper": [
        ExerciseTemplate("dumbbell incline chest press", 3, "8-12"),
        ExerciseTemplate("chest supported row", 3, "8-12"),
        ExerciseTemplate("lat pulldown", 3, "8-12"),
        ExerciseTemplate("cable lateral raise", 3, "12-20"),
        ExerciseTemplate("preacher curl", 2, "10-15"),
        ExerciseTemplate("cable tricep pushdown", 2, "10-15"),
    ],
    "Rest": [
        ExerciseTemplate(
            "incline treadmill walk",
            1,
            "20-30 min",
            "Keep it easy and nasal-breathable.",
        ),
    ],
}


class WorkoutPlanner:
    """Generate structured workout plans without relying on the LLM."""

    def __init__(self, plans: WorkoutPlanRepository) -> None:
        self.plans = plans

    def generate(self, user_id: str, request: WorkoutPlanRequest) -> models.WorkoutPlan:
        """Generate and persist a workout plan."""

        split_day, focus = self._split_for_date(request.requested_for)
        exercises = self._adapt_exercises(split_day, request)
        duration = request.available_minutes or (30 if split_day == "Rest" else 75)
        rationale = self._build_rationale(split_day, request, duration)

        return self.plans.add(
            models.WorkoutPlan(
                user_id=user_id,
                scheduled_for=request.requested_for,
                split_day=split_day,
                focus=focus,
                duration_minutes=duration,
                exercises=[asdict(exercise) for exercise in exercises],
                rationale=rationale,
            )
        )

    def _split_for_date(self, requested_for: datetime) -> tuple[str, str]:
        return SPLIT_BY_WEEKDAY[requested_for.weekday()]

    def _adapt_exercises(
        self, split_day: str, request: WorkoutPlanRequest
    ) -> list[ExerciseTemplate]:
        exercises = list(EXERCISES[split_day])
        flags = " ".join([*request.soreness, *request.injuries, request.recovery_notes]).lower()

        if request.available_minutes is not None and request.available_minutes < 60:
            exercises = exercises[: max(2, min(4, len(exercises)))]

        if "shoulder" in flags:
            exercises = [
                exercise
                for exercise in exercises
                if "shoulder press" not in exercise.name and "front delt" not in exercise.name
            ]

        if "knee" in flags and split_day in {"Lower", "Legs + Rear Delts"}:
            exercises = [
                ExerciseTemplate(
                    exercise.name,
                    max(2, exercise.sets - 1),
                    exercise.reps,
                    "Use pain-free range of motion and stop for meaningful joint pain.",
                )
                for exercise in exercises
            ]

        return exercises

    def _build_rationale(
        self, split_day: str, request: WorkoutPlanRequest, duration_minutes: int
    ) -> str:
        if split_day == "Rest":
            return "Scheduled rest day; light cardio is optional to maintain consistency."
        constraints = []
        if request.available_minutes:
            constraints.append(f"available time: {duration_minutes} minutes")
        if request.soreness:
            constraints.append(f"soreness: {', '.join(request.soreness)}")
        if request.injuries:
            constraints.append(f"injuries to respect: {', '.join(request.injuries)}")
        suffix = f" Adjusted for {', '.join(constraints)}." if constraints else ""
        return (
            "Plan follows the preferred split, keeps familiar exercises, and uses reps before "
            f"load as the default progression.{suffix}"
        )
