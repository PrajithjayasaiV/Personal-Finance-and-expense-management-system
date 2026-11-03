"""Shared pytest fixtures: every test gets a brand-new temporary SQLite database."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest
from sqlalchemy.orm import Session

import config
import database
from services import auth_service
from tests.helpers import make_user


@pytest.fixture(scope="session", autouse=True)
def _fast_password_hashing():
    """Real hashing uses 600k iterations; tests use 1k so the suite stays fast."""
    original = config.PASSWORD_HASH_METHOD
    config.PASSWORD_HASH_METHOD = "pbkdf2:sha256:1000"
    yield
    config.PASSWORD_HASH_METHOD = original


@pytest.fixture(autouse=True)
def _reset_throttle():
    auth_service.reset_login_throttle()
    yield
    auth_service.reset_login_throttle()


@pytest.fixture()
def engine(tmp_path):
    eng = database.make_engine(tmp_path / "test.db")
    database.init_db(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def db(engine):
    session = Session(engine, expire_on_commit=False)
    yield session
    session.rollback()
    session.close()


@pytest.fixture()
def alice(db):
    return make_user(db, "Alice Rao", "alice@example.com")


@pytest.fixture()
def bob(db):
    return make_user(db, "Bob Iyer", "bob@example.com")
