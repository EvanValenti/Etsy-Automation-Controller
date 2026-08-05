"""SQLite engine + session factory + schema creation.

This module owns the one SQLModel `Engine` object for the process and the
`create_db_and_tables()` call invoked once at FastAPI startup (see
`api/main.py`'s lifespan handler). Table creation only — no query logic
lives here; that belongs to `sqlite_repositories.py`.

The DB file path defaults to `<project root>/automation_controller.db`
and is overridable via the `AUTOMATION_CONTROLLER_DB_PATH` environment
variable (e.g. for tests, which should point this at a temp path or
`:memory:` rather than the real file).
"""

from __future__ import annotations

import os
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine

# Import registers every table class on SQLModel.metadata before
# create_db_and_tables() runs — required even though `models` is
# otherwise unused directly in this module.
from infra.db import models as _models  # noqa: F401

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_DB_PATH = _PROJECT_ROOT / "automation_controller.db"

DB_PATH = os.environ.get("AUTOMATION_CONTROLLER_DB_PATH", str(_DEFAULT_DB_PATH))
DATABASE_URL = f"sqlite:///{DB_PATH}" if DB_PATH != ":memory:" else "sqlite://"

engine = create_engine(DATABASE_URL, echo=False, connect_args={"check_same_thread": False})


def create_db_and_tables() -> None:
    """Schema/table creation only. Idempotent — SQLAlchemy's create_all()
    issues CREATE TABLE IF NOT EXISTS semantics, so calling this more than
    once (e.g. across test runs against the same file) is safe.
    """
    SQLModel.metadata.create_all(engine)


def get_session() -> Session:
    """One Session per call. FastAPI dependency wrapping this lives in
    api/dependencies.py, which also handles the generator/close lifecycle.
    """
    return Session(engine)
