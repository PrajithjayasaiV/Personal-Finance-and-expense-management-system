"""Database initialisation, persistence and integrity."""
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

import config
import database
from services import analytics_service as an
from tests.helpers import expense, income, make_user


def test_database_is_created_from_scratch_and_enforces_foreign_keys(tmp_path):
    path = tmp_path / "nested" / "folder" / "fresh.db"        # parent folders do not exist yet
    engine = database.make_engine(path)
    database.init_db(engine)
    assert path.exists()
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
        tables = {r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
        assert {"users", "categories", "transactions", "budgets", "savings_goals", "goal_contributions",
                "notifications", "insight_feedback"} <= tables
        assert conn.execute(text("PRAGMA user_version")).scalar() == database.SCHEMA_VERSION
    engine.dispose()


def test_data_survives_restart_and_reinitialisation_never_deletes(tmp_path):
    path = tmp_path / "persist.db"
    first = database.make_engine(path)
    database.init_db(first)
    with Session(first) as s:
        user = make_user(s, opening=500)
        income(s, user, 1000, "Salary", date(2026, 10, 1))
        expense(s, user, 250, "Food", date(2026, 10, 2))
        s.commit()
        uid = user.id
    first.dispose()                                           # "application restart"

    second = database.make_engine(path)
    database.init_db(second)                                  # running initialisation again must be harmless
    database.init_db(second)
    with Session(second) as s:
        assert an.current_balance(s, uid) == 125_000          # 500 + 1000 - 250
    second.dispose()


def test_committed_and_rolled_back_work_in_session_scope(engine):
    database.set_engine(engine)
    with database.session_scope() as db:
        make_user(db, email="kept@example.com")
    with pytest.raises(RuntimeError):
        with database.session_scope() as db:
            make_user(db, email="lost@example.com")
            raise RuntimeError("boom")
    with Session(engine) as s:
        emails = {r[0] for r in s.execute(text("SELECT email FROM users"))}
    assert emails == {"kept@example.com"}


def test_default_database_path_is_stable_and_inside_the_project():
    assert config.DB_PATH.is_absolute()
    assert config.DB_PATH.parent == config.DATA_DIR or "EXPENSE_TRACKER_DB_PATH" in __import__("os").environ
    assert config.BASE_DIR == Path(config.__file__).resolve().parent


def test_money_check_constraints_reject_bad_rows_at_database_level(db, alice):
    from sqlalchemy.exc import IntegrityError
    with pytest.raises(IntegrityError):
        db.execute(text("INSERT INTO budgets (user_id, month, category_id, limit_amount, description, created_at, updated_at) "
                        "VALUES (:u, '2026-10', NULL, 0, '', '2026-01-01', '2026-01-01')"), {"u": alice.id})
    db.rollback()
