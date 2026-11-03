"""User A must never read, change, delete or export user B's data by supplying B's record IDs."""
from datetime import date

import pytest

from services import analytics_service as an
from services import budget_service as bs
from services import insights_service as ins
from services import notification_service as ns
from services import report_service as rs
from services import savings_service as sv
from services import transaction_service as ts
from tests.helpers import TODAY, cat_id, expense, income
from utils.errors import AuthorizationError

MONTH = "2026-10"


@pytest.fixture()
def world(db, alice, bob):
    """Bob owns one of everything; Alice owns a little data of her own."""
    bob_cat = ts.create_category(db, bob.id, "Bob Hobby", "expense")
    bob_txn = expense(db, bob, 1234, "Food", date(2026, 10, 2), desc="BOB-SECRET-LUNCH")
    income(db, bob, 90000, "Salary", date(2026, 10, 1), desc="BOB-SALARY")
    bob_budget = bs.create_budget(db, bob.id, MONTH, 100, cat_id(db, bob, "Food"))
    bob_goal = sv.create_goal(db, bob.id, name="BOB-GOAL", target_amount=5000, today=TODAY)
    bob_contrib = sv.add_contribution(db, bob.id, bob_goal.id, 100, TODAY, "BOB-NOTE", today=TODAY)
    expense(db, bob, 500, "Food", date(2026, 10, 3))                  # pushes Bob's budget over -> notification
    bob_note = ns.list_notifications(db, bob.id)[0]
    expense(db, alice, 10, "Food", date(2026, 10, 2), desc="alice-lunch")
    return dict(cat=bob_cat, txn=bob_txn, budget=bob_budget, goal=bob_goal, contrib=bob_contrib, note=bob_note)


def test_transactions_are_protected(db, alice, world):
    t = world["txn"]
    with pytest.raises(AuthorizationError):
        ts.get_transaction(db, alice.id, t.id)
    with pytest.raises(AuthorizationError):
        ts.update_transaction(db, alice.id, t.id, txn_type="expense", amount=1, category_id=cat_id(db, alice, "Food"),
                              txn_date=TODAY, payment_method="Cash", today=TODAY)
    with pytest.raises(AuthorizationError):
        ts.delete_transaction(db, alice.id, t.id)
    db.refresh(t)
    assert t.amount == 123_400 and t.description == "BOB-SECRET-LUNCH"      # untouched


def test_cannot_use_another_users_category_anywhere(db, alice, world):
    bob_cat = world["cat"].id
    with pytest.raises(AuthorizationError):
        ts.create_transaction(db, alice.id, txn_type="expense", amount=5, category_id=bob_cat, txn_date=TODAY,
                              payment_method="Cash", today=TODAY)
    with pytest.raises(AuthorizationError):
        bs.create_budget(db, alice.id, MONTH, 100, bob_cat)
    with pytest.raises(AuthorizationError):
        ts.get_category(db, alice.id, bob_cat)
    with pytest.raises(AuthorizationError):
        ts.rename_category(db, alice.id, bob_cat, "Mine now")
    with pytest.raises(AuthorizationError):
        ts.delete_category(db, alice.id, bob_cat)
    with pytest.raises(AuthorizationError):    # also cannot reassign her own transactions into Bob's category
        expense(db, alice, 10, "Shopping", TODAY)
        ts.delete_category(db, alice.id, cat_id(db, alice, "Shopping"), reassign_to_id=bob_cat)
    assert ts.get_category(db, world["cat"].user_id, bob_cat).name == "Bob Hobby"


def test_budgets_are_protected(db, alice, world):
    b = world["budget"].id
    for call in (lambda: bs.get_budget(db, alice.id, b), lambda: bs.update_budget(db, alice.id, b, limit=1),
                 lambda: bs.delete_budget(db, alice.id, b)):
        with pytest.raises(AuthorizationError):
            call()
    assert world["budget"].limit_amount == 10_000


def test_goals_and_contributions_are_protected(db, alice, world):
    g, c = world["goal"].id, world["contrib"].id
    calls = [
        lambda: sv.get_goal(db, alice.id, g),
        lambda: sv.update_goal(db, alice.id, g, name="x", target_amount=1, start_date=TODAY, target_date=None),
        lambda: sv.set_goal_paused(db, alice.id, g, True),
        lambda: sv.delete_goal(db, alice.id, g),
        lambda: sv.add_contribution(db, alice.id, g, 10, TODAY, today=TODAY),
        lambda: sv.list_contributions(db, alice.id, g),
        lambda: sv.get_contribution(db, alice.id, c),
        lambda: sv.update_contribution(db, alice.id, c, 1, TODAY, today=TODAY),
        lambda: sv.delete_contribution(db, alice.id, c),
    ]
    for call in calls:
        with pytest.raises(AuthorizationError):
            call()
    assert sv.saved_amount(db, world["goal"].user_id, g) == 10_000


def test_database_rejects_contribution_to_another_users_goal(db, alice, world):
    from sqlalchemy.exc import IntegrityError
    from models import GoalContribution
    db.add(GoalContribution(user_id=alice.id, goal_id=world["goal"].id, amount=100, contribution_date=TODAY))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_notifications_are_protected_and_listed_per_user(db, alice, bob, world):
    with pytest.raises(AuthorizationError):
        ns.set_read(db, alice.id, world["note"].id)
    assert world["note"].id not in {n.id for n in ns.list_notifications(db, alice.id)}
    assert ns.unread_count(db, alice.id) == 0 and ns.unread_count(db, bob.id) >= 1
    assert ns.mark_all_read(db, alice.id) == 0
    assert ns.unread_count(db, bob.id) >= 1                      # Alice's call did not touch Bob's


def test_reads_and_calculations_are_scoped(db, alice, bob, world):
    rows, total = ts.query_transactions(db, alice.id)
    assert total == 1 and rows[0].description == "alice-lunch"
    assert ts.query_transactions(db, alice.id, ts.TransactionFilters(search="BOB"))[1] == 0
    assert an.period_totals(db, alice.id).income == 0 and an.current_balance(db, alice.id) == -1000
    assert [c.name for c in an.category_totals(db, alice.id, None, None)] == ["Food"]
    assert bs.month_statuses(db, alice.id, MONTH) == []
    assert sv.list_goals(db, alice.id) == [] and sv.contributions_by_month(db, alice.id) == {}
    assert ins.generate_insights(db, alice.id, date(2026, 10, 1), TODAY, today=TODAY) == []
    assert "Bob Hobby" not in {c.name for c in ts.list_categories(db, alice.id)}


def test_exports_contain_only_the_requesting_users_rows(db, alice, bob, world):
    blobs = [
        rs.export_transactions_csv(db, alice.id)[0], rs.export_budgets_csv(db, alice.id, "2026-09", "2026-11")[0],
        rs.export_contributions_csv(db, alice.id)[0],
        rs.summary_csv(rs.build_summary(db, alice.id, date(2026, 10, 1), TODAY)),
    ]
    for blob in blobs:
        text = blob.decode("utf-8-sig")
        assert "BOB" not in text and "Bob" not in text and "bob@" not in text
    assert "alice-lunch" in blobs[0].decode("utf-8-sig")
    bobs = rs.export_transactions_csv(db, bob.id)[0].decode("utf-8-sig")
    assert "BOB-SECRET-LUNCH" in bobs and "alice-lunch" not in bobs


def test_settings_updates_only_touch_the_given_user(db, alice, bob):
    from services import auth_service
    auth_service.update_settings(db, alice.id, full_name="Alice Changed", min_reserve=999)
    db.refresh(bob)
    assert bob.full_name == "Bob Iyer" and bob.min_reserve == 0
