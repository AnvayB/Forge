from __future__ import annotations

from sqlalchemy import inspect, text

from fitness_coach.database.session import create_db_engine, init_db


def test_init_db_adds_missing_columns_to_existing_sqlite_workout_table(tmp_path) -> None:
    engine = create_db_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE workout_events ("
                "id VARCHAR(36) PRIMARY KEY, user_id VARCHAR(36), "
                "occurred_at DATETIME, workout_type VARCHAR(120))"
            )
        )

    init_db(engine)

    columns = {column["name"] for column in inspect(engine).get_columns("workout_events")}
    assert {"calories_burned", "exercises", "proof_source", "notes"} <= columns
    engine.dispose()
