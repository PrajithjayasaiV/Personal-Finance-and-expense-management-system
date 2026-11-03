"""UI smoke tests: run every page script headlessly with Streamlit's AppTest.

These do not click through a browser, but they execute each page end to end
against a real (temporary) database, so import errors, wrong service calls and
crashes on empty or populated data are caught.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import database
from components.navigation import APP_PAGES
from services import auth_service
from services import budget_service as bs
from services import savings_service as sv
from tests.helpers import cat_id, make_user

ROOT = Path(__file__).resolve().parents[1]
PAGE_FILES = [path for path, *_ in APP_PAGES]


@pytest.fixture()
def app_db(engine, monkeypatch):
    """Point the application's global session factory at the temporary database."""
    monkeypatch.setattr(database, "_engine", None)
    monkeypatch.setattr(database, "_factory", None)
    database.set_engine(engine)
    yield engine
    monkeypatch.setattr(database, "_engine", None)
    monkeypatch.setattr(database, "_factory", None)


def seed(db, user):
    """A month-to-date + previous-month history, budgets and a goal, relative to the real clock."""
    from services import transaction_service as ts
    today = date.today()
    first = today.replace(day=1)
    prev_first = (first - timedelta(days=1)).replace(day=1)
    day = lambda base, n: min(base + timedelta(days=n), today)  # noqa: E731

    def add(kind, amount, category, when, method="UPI", desc=""):
        ts.create_transaction(db, user.id, txn_type=kind, amount=amount, category_id=cat_id(db, user, category, kind),
                              txn_date=when, payment_method=method, description=desc, today=today)

    add("income", 50000, "Salary", prev_first, "Bank Transfer", "Pay")
    add("expense", 1500, "Food", day(prev_first, 2), desc="Eating out")
    add("expense", 12000, "Rent", day(prev_first, 1), "Bank Transfer", "Rent")
    add("expense", 600, "Transportation", day(prev_first, 3))
    add("income", 52000, "Salary", first, "Bank Transfer", "Pay")
    add("expense", 4000, "Food", first, desc="Dinner =cmd")
    add("expense", 12000, "Rent", first, "Bank Transfer", "Rent")
    add("expense", 800, "Transportation", first)
    month = f"{today:%Y-%m}"
    bs.create_budget(db, user.id, month, 20000)
    bs.create_budget(db, user.id, month, 3000, cat_id(db, user, "Food"))
    goal = sv.create_goal(db, user.id, name="Laptop", target_amount=60000, start_date=today - timedelta(days=45),
                          target_date=today + timedelta(days=150), initial_saved=5000, today=today)
    sv.add_contribution(db, user.id, goal.id, 2500, today, "bonus", today=today)


def run_page(path: str, user_id: int | None) -> AppTest:
    at = AppTest.from_file(str(ROOT / path), default_timeout=30)
    if user_id is not None:
        at.session_state[auth_service.SESSION_USER_KEY] = user_id
    return at.run()


def assert_clean(at: AppTest, label: str = "") -> None:
    assert not at.exception, f"{label} raised: {[e.value for e in at.exception]}"
    assert not at.error, f"{label} showed errors: {[e.value for e in at.error]}"


@pytest.mark.parametrize("path", PAGE_FILES)
def test_page_renders_for_new_empty_account(app_db, path):
    from sqlalchemy.orm import Session
    with Session(app_db) as s:
        user = make_user(s, email="new@example.com")
        s.commit()
        uid = user.id
    assert_clean(run_page(path, uid), path)


@pytest.mark.parametrize("path", PAGE_FILES)
def test_page_renders_with_data(app_db, path):
    from sqlalchemy.orm import Session
    with Session(app_db) as s:
        user = make_user(s, email="full@example.com", opening=2000)
        seed(s, user)
        s.commit()
        uid = user.id
    assert_clean(run_page(path, uid), path)


@pytest.mark.parametrize("path", PAGE_FILES)
def test_protected_pages_refuse_signed_out_visitors(app_db, path):
    at = run_page(path, None)
    assert not at.exception
    assert any("sign in" in w.value.lower() for w in at.warning)
    assert len(at.metric) == 0 and not at.dataframe       # nothing financial rendered
