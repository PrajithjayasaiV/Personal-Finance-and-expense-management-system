from datetime import date

import pytest
from sqlalchemy import select

from models import Notification
from services import budget_service as bs
from services import notification_service as ns
from services import transaction_service as ts
from tests.helpers import TODAY, cat_id, expense, income
from utils.errors import ConflictError, ValidationError

MONTH = "2026-10"
D = date(2026, 10, 5)


def status(db, user, **kw):
    return next(s for s in bs.month_statuses(db, user.id, MONTH) if s.category_id == kw.get("category_id"))


def notes(db, user, active_only=True):
    stmt = select(Notification).where(Notification.user_id == user.id)
    if active_only:
        stmt = stmt.where(Notification.is_active.is_(True))
    return list(db.scalars(stmt.order_by(Notification.dedupe_key)))


def test_create_overall_and_category_budgets(db, alice):
    overall = bs.create_budget(db, alice.id, MONTH, 20000, description="October plan")
    food = bs.create_budget(db, alice.id, MONTH, 5000, cat_id(db, alice, "Food"))
    assert overall.category_id is None and overall.limit_amount == 2_000_000
    assert food.limit_amount == 500_000
    labels = [label for _, label in bs.list_budgets(db, alice.id, MONTH)]
    assert labels == ["Overall budget", "Food"]


def test_duplicate_budgets_prevented(db, alice):
    bs.create_budget(db, alice.id, MONTH, 20000)
    with pytest.raises(ConflictError):
        bs.create_budget(db, alice.id, MONTH, 999)
    food = cat_id(db, alice, "Food")
    bs.create_budget(db, alice.id, MONTH, 5000, food)
    with pytest.raises(ConflictError):
        bs.create_budget(db, alice.id, MONTH, 6000, food)
    bs.create_budget(db, alice.id, "2026-11", 20000)                 # a different month is fine
    assert len(bs.list_budgets(db, alice.id, MONTH)) == 2


def test_database_blocks_duplicate_overall_budget_even_with_null_category(db, alice):
    from sqlalchemy.exc import IntegrityError
    from models import Budget
    db.add(Budget(user_id=alice.id, month=MONTH, category_id=None, limit_amount=100))
    db.flush()
    db.add(Budget(user_id=alice.id, month=MONTH, category_id=None, limit_amount=200))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_budget_validation(db, alice):
    for bad in (0, -10, "x", None):
        with pytest.raises(ValidationError):
            bs.create_budget(db, alice.id, MONTH, bad)
    with pytest.raises(ValidationError):
        bs.create_budget(db, alice.id, "2026-13", 100)
    with pytest.raises(ValidationError, match="expense categories"):
        bs.create_budget(db, alice.id, MONTH, 100, cat_id(db, alice, "Salary", "income"))
    with pytest.raises(ValidationError):
        bs.create_budget(db, alice.id, MONTH, 100, 424242)


@pytest.mark.parametrize("spent,level,pct", [
    (0, bs.OK, 0.0),
    (799.99, bs.OK, 80.0),    # 79.999% displays as 80.0 but is still below the threshold
    (800, bs.WARNING, 80.0),
    (999.99, bs.WARNING, 100.0),
    (1000, bs.EXCEEDED, 100.0),
    (1000.01, bs.EXCEEDED, 100.0),
    (1500, bs.EXCEEDED, 150.0),
])
def test_threshold_levels_use_exact_arithmetic(db, alice, spent, level, pct):
    bs.create_budget(db, alice.id, MONTH, 1000)
    if spent:
        expense(db, alice, spent, "Food", D)
    s = status(db, alice)
    assert s.level == level
    assert s.utilization == pct


def test_remaining_and_exceeded_amounts(db, alice):
    bs.create_budget(db, alice.id, MONTH, 1000)
    expense(db, alice, 1250, "Food", D)
    s = status(db, alice)
    assert (s.limit, s.used, s.remaining, s.exceeded_by) == (100_000, 125_000, -25_000, 25_000)
    expense(db, alice, 100, "Food", D)   # still shows the exceeded budget, never hidden
    assert status(db, alice).exceeded_by == 35_000


def test_category_budget_counts_only_that_category(db, alice):
    food = cat_id(db, alice, "Food")
    bs.create_budget(db, alice.id, MONTH, 1000, food)
    bs.create_budget(db, alice.id, MONTH, 5000)
    expense(db, alice, 300, "Food", D)
    expense(db, alice, 900, "Rent", D)
    expense(db, alice, 50, "Food", date(2026, 9, 30))        # other month - ignored
    income(db, alice, 9999, "Salary", D)                     # income - ignored
    assert status(db, alice, category_id=food).used == 30_000
    assert status(db, alice).used == 120_000


def test_status_follows_edits_and_deletes(db, alice):
    bs.create_budget(db, alice.id, MONTH, 1000)
    txn = expense(db, alice, 900, "Food", D)
    assert status(db, alice).level == bs.WARNING
    ts.update_transaction(db, alice.id, txn.id, txn_type="expense", amount=1200, category_id=cat_id(db, alice, "Food"),
                          txn_date=D, payment_method="UPI", today=TODAY)
    assert status(db, alice).level == bs.EXCEEDED
    ts.update_transaction(db, alice.id, txn.id, txn_type="expense", amount=100, category_id=cat_id(db, alice, "Food"),
                          txn_date=D, payment_method="UPI", today=TODAY)
    assert status(db, alice).level == bs.OK
    ts.update_transaction(db, alice.id, txn.id, txn_type="expense", amount=900, category_id=cat_id(db, alice, "Food"),
                          txn_date=date(2026, 9, 1), payment_method="UPI", today=TODAY)   # moved to another month
    assert status(db, alice).used == 0
    ts.delete_transaction(db, alice.id, txn.id)
    assert status(db, alice).level == bs.OK


def test_update_and_delete_budget(db, alice):
    b = bs.create_budget(db, alice.id, MONTH, 1000)
    expense(db, alice, 900, "Food", D)
    bs.update_budget(db, alice.id, b.id, limit=3000, description="Raised")
    assert status(db, alice).level == bs.OK and status(db, alice).description == "Raised"
    bs.delete_budget(db, alice.id, b.id)
    assert bs.month_statuses(db, alice.id, MONTH) == []


def test_upsert_creates_then_updates(db, alice):
    food = cat_id(db, alice, "Food")
    b1, created1 = bs.upsert_budget(db, alice.id, MONTH, 1000, food)
    b2, created2 = bs.upsert_budget(db, alice.id, MONTH, 1500, food)
    assert created1 and not created2 and b1.id == b2.id and b2.limit_amount == 150_000


# ------------------------------------------------------------------ notifications
def test_warning_then_exceeded_notifications_without_duplicates(db, alice):
    bs.create_budget(db, alice.id, MONTH, 1000)
    expense(db, alice, 850, "Food", D)
    keys = [n.dedupe_key for n in notes(db, alice)]
    assert len(keys) == 1 and keys[0].startswith("budget_warning:")
    assert "Approaching" in notes(db, alice)[0].title

    for _ in range(5):                                    # simulated Streamlit reruns
        ns.refresh_all(db, alice.id, TODAY)
        ns.sync_budget_notifications(db, alice.id, [MONTH])
    assert len(notes(db, alice, active_only=False)) == 1

    expense(db, alice, 300, "Food", D)                    # now exceeded
    active = notes(db, alice)
    assert [n.dedupe_key.split(":")[0] for n in active] == ["budget_exceeded"]
    assert "over by" in active[0].message.lower()
    assert len(notes(db, alice, active_only=False)) == 2  # the warning row is kept but switched off


def test_notification_clears_when_spending_drops_and_returns_unread(db, alice):
    bs.create_budget(db, alice.id, MONTH, 1000)
    txn = expense(db, alice, 1100, "Food", D)
    n = notes(db, alice)[0]
    ns.set_read(db, alice.id, n.id)
    assert ns.unread_count(db, alice.id) == 0
    ts.delete_transaction(db, alice.id, txn.id)
    assert notes(db, alice) == [] and ns.unread_count(db, alice.id) == 0
    expense(db, alice, 1100, "Food", D)                   # condition returns
    again = notes(db, alice)
    assert len(again) == 1 and again[0].id == n.id and again[0].is_read is False


def test_category_budget_notification_mentions_category(db, alice):
    bs.create_budget(db, alice.id, MONTH, 500, cat_id(db, alice, "Food"))
    expense(db, alice, 500, "Food", D)
    n = notes(db, alice)[0]
    assert "Food" in n.title and n.ref_type == "budget"


def test_notifications_respect_preference_and_budget_deletion(db, alice):
    from services import auth_service
    b = bs.create_budget(db, alice.id, MONTH, 100)
    expense(db, alice, 150, "Food", D)
    assert len(notes(db, alice)) == 1
    bs.delete_budget(db, alice.id, b.id)
    assert notes(db, alice, active_only=False) == []
    auth_service.update_settings(db, alice.id, notify_budget=False)
    bs.create_budget(db, alice.id, MONTH, 100)
    assert notes(db, alice, active_only=False) == []


def test_mark_read_all_and_listing(db, alice):
    bs.create_budget(db, alice.id, MONTH, 100)
    bs.create_budget(db, alice.id, MONTH, 100, cat_id(db, alice, "Food"))
    expense(db, alice, 150, "Food", D)
    assert ns.unread_count(db, alice.id) == 2
    assert ns.mark_all_read(db, alice.id) == 2
    assert ns.unread_count(db, alice.id) == 0
    assert len(ns.list_notifications(db, alice.id)) == 2
    assert ns.list_notifications(db, alice.id, unread_only=True) == []
