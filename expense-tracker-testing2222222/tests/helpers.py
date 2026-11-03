"""Small builders used across the test modules."""
from __future__ import annotations

from datetime import date

from sqlalchemy import select

from models import Category
from services import auth_service
from services import transaction_service as ts

TODAY = date(2026, 10, 24)   # all tests run "as of" this date, independent of the real clock
PASSWORD = "Secret123"


def make_user(db, name="Test User", email="test@example.com", opening=0):
    return auth_service.register_user(db, name, email, PASSWORD, PASSWORD, opening_balance=opening)


def cat_id(db, user, name, kind="expense"):
    return db.scalar(select(Category.id).where(
        Category.user_id == user.id, Category.name == name, Category.type == kind))


def add_txn(db, user, kind, amount, category, when, method="UPI", desc=""):
    return ts.create_transaction(
        db, user.id, txn_type=kind, amount=amount, category_id=cat_id(db, user, category, kind),
        txn_date=when, payment_method=method, description=desc, today=TODAY)


def expense(db, user, amount, category, when, **kw):
    return add_txn(db, user, "expense", amount, category, when, **kw)


def income(db, user, amount, category, when, **kw):
    return add_txn(db, user, "income", amount, category, when, **kw)
