"""Database engine, sessions and safe initialisation.

* One SQLite file at a stable, configurable path (see ``config.DB_PATH``).
* Foreign keys are enforced on every connection.
* ``init_db`` is idempotent: it only creates what is missing and never drops data.
* Services receive a ``Session`` and never commit; ``session_scope`` owns the
  unit of work so related changes succeed or fail together.
"""
from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

import config
from models import Base

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
# Future schema changes: add ``N: callable(connection)`` here and bump
# SCHEMA_VERSION. Migrations run in order, once, without deleting user data.
MIGRATIONS: dict[int, Callable] = {}

_lock = threading.Lock()
_engine: Engine | None = None
_factory: sessionmaker[Session] | None = None


def make_engine(db_path: str | Path) -> Engine:
    """Create an engine for ``db_path`` with the required SQLite settings."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{path.as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 15},
        future=True,
    )

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn, _record):
        # Take control of transactions so SAVEPOINTs behave (SQLAlchemy pysqlite recipe).
        dbapi_conn.isolation_level = None
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()

    @event.listens_for(engine, "begin")
    def _on_begin(conn):
        conn.exec_driver_sql("BEGIN")

    return engine


def init_db(engine: Engine) -> None:
    """Create missing tables/indexes and apply pending migrations (never destructive)."""
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        version = conn.exec_driver_sql("PRAGMA user_version").scalar() or 0
        for target in range(version + 1, SCHEMA_VERSION + 1):
            migration = MIGRATIONS.get(target)
            if migration:
                log.info("Applying database migration %s", target)
                migration(conn)
        if version != SCHEMA_VERSION:
            conn.exec_driver_sql(f"PRAGMA user_version={SCHEMA_VERSION}")


def set_engine(engine: Engine) -> None:
    """Point the application at a specific engine (used by tests)."""
    global _engine, _factory
    with _lock:
        _engine = engine
        _factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)


def get_engine() -> Engine:
    """Return the shared engine, creating and initialising the database on first use."""
    global _engine, _factory
    if _engine is None:
        with _lock:
            if _engine is None:
                engine = make_engine(config.DB_PATH)
                init_db(engine)
                _engine = engine
                _factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
                log.info("Database ready at %s", config.DB_PATH)
    return _engine


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope: commit on success, roll back on any error.

    Do not call ``st.rerun()``/``st.stop()`` inside the block (they raise
    control-flow exceptions that would trigger a rollback).
    """
    get_engine()
    assert _factory is not None
    db = _factory()
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()
