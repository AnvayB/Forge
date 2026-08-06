"""SQLAlchemy engine and session management."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from fitness_coach.database.models import Base

logger = logging.getLogger(__name__)


def _ensure_sqlite_parent(database_url: str) -> None:
    if not database_url.startswith("sqlite:///"):
        return
    db_path = Path(database_url.replace("sqlite:///", "", 1))
    if db_path.parent != Path("."):
        db_path.parent.mkdir(parents=True, exist_ok=True)


def create_db_engine(database_url: str) -> Engine:
    """Create a SQLAlchemy engine with SQLite-friendly defaults."""

    _ensure_sqlite_parent(database_url)
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    return create_engine(database_url, future=True, connect_args=connect_args)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create a session factory for dependency injection."""

    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db(engine: Engine) -> None:
    """Create all tables, then add any model columns missing from an existing database."""

    Base.metadata.create_all(engine)
    if engine.dialect.name == "sqlite":
        _sync_missing_columns(engine)


def _sync_missing_columns(engine: Engine) -> None:
    """Add columns declared on models but absent from the database file on disk.

    This project has no migration tool, so a deployed SQLite file can lag behind the
    ORM models after a schema change. This keeps it usable without recreating the file.
    """

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as connection:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            existing_columns = {col["name"] for col in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing_columns:
                    continue
                ddl_type = column.type.compile(engine.dialect)
                logger.warning(
                    "adding_missing_column table=%s column=%s", table.name, column.name
                )
                connection.execute(
                    text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {ddl_type}')
                )


@contextmanager
def session_scope(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    """Provide a transactional scope around repository operations."""

    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
