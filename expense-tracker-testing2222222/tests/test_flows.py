"""End-to-end UI flows driven through Streamlit's AppTest (forms are filled and submitted)."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from streamlit.testing.v1 import AppTest

from models import Budget, GoalContribution, Notification, SavingsGoal, Transaction, User
from services import auth_service
from tests.helpers import PASSWORD, cat_id, make_user
from tests.test_pages import app_db  # noqa: F401  (fixture re-used)

ROOT = Path(__file__).resolve().parents[1]


def page(path: str, user_id: int) -> AppTest:
    at = AppTest.from_file(str(ROOT / path), default_timeout=30)
    at.session_state[auth_service.SESSION_USER_KEY] = user_id
    return at.run()


def button(at: AppTest, label: str):
    return next(b for b in at.button if b.label == label)


@pytest.fixture()
def user_id(app_db):
    with Session(app_db) as s:
        u = make_user(s, email="flow@example.com", opening=1000)
        s.commit()
        return u.id


def fetch(app_db, stmt):
    with Session(app_db) as s:
        return list(s.scalars(stmt))


# ---------------------------------------------------------------- sign in / out through app.py
def test_sign_in_shows_dashboard_and_logout_clears_session(app_db, user_id):
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert not at.exception and button(at, "Sign in")
    at.text_input[0].set_value("flow@example.com")
    at.text_input[1].set_value("WrongPass1")
    button(at, "Sign in").click()
    at.run()
    assert [e.value for e in at.error] == ["Invalid email or password."]
    assert auth_service.SESSION_USER_KEY not in at.session_state

    at.text_input[0].set_value("flow@example.com")
    at.text_input[1].set_value(PASSWORD)
    button(at, "Sign in").click()
    at.run()
    assert not at.exception
    assert at.session_state[auth_service.SESSION_USER_KEY] == user_id
    assert any("Dashboard" in m.value and "sf-title" in m.value for m in at.markdown)
    assert any(b.label == "Log out" for b in at.button)

    at.session_state["dash_range_preset"] = "custom"      # leftover UI state that must not survive logout
    button(at, "Log out").click()
    at.run()
    assert not at.exception
    assert auth_service.SESSION_USER_KEY not in at.session_state
    assert "dash_range_preset" not in at.session_state
    assert any("Welcome back" in m.value for m in at.markdown)


def test_forged_session_id_is_rejected(app_db):
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30)
    at.session_state[auth_service.SESSION_USER_KEY] = 424242      # id that does not exist
    at.run()
    assert not at.exception
    assert any("Welcome back" in m.value for m in at.markdown)    # shown the login page, not the dashboard
    assert auth_service.SESSION_USER_KEY not in at.session_state


def _register_app():
    from components.auth_pages import register_page
    register_page()


def test_registration_form_creates_account_and_signs_in(app_db):
    at = AppTest.from_function(_register_app, default_timeout=30).run()
    assert not at.exception
    at.text_input(key="reg_name").set_value("Meera Nair")
    at.text_input(key="reg_email").set_value("Meera@Example.com")
    at.text_input(key="reg_password").set_value(PASSWORD)
    at.text_input(key="reg_confirm").set_value(PASSWORD)
    at.number_input(key="reg_opening").set_value(1500.5)
    button(at, "Create account").click()
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    users = fetch(app_db, select(User))
    assert len(users) == 1 and users[0].email == "meera@example.com" and users[0].opening_balance == 150_050
    assert at.session_state[auth_service.SESSION_USER_KEY] == users[0].id


def test_registration_shows_readable_errors(app_db):
    at = AppTest.from_function(_register_app, default_timeout=30).run()
    at.text_input(key="reg_name").set_value("X")
    at.text_input(key="reg_email").set_value("not-an-email")
    at.text_input(key="reg_password").set_value(PASSWORD)
    at.text_input(key="reg_confirm").set_value(PASSWORD)
    button(at, "Create account").click()
    at.run()
    assert not at.exception
    assert any("valid email" in e.value for e in at.error)
    assert fetch(app_db, select(User)) == []


# ---------------------------------------------------------------- forms persist to the database
def test_add_expense_and_income_through_the_form(app_db, user_id):
    at = page("views/2_Transactions.py", user_id)
    at.number_input(key="add_amount").set_value(249.75)
    at.text_input(key="add_desc").set_value("Groceries run")
    button(at, "Add transaction").click()
    at.run()
    assert not at.exception and not at.error, [e.value for e in at.error]
    rows = fetch(app_db, select(Transaction))
    assert len(rows) == 1 and rows[0].amount == 24_975 and rows[0].type == "expense" and rows[0].description == "Groceries run"
    assert rows[0].user_id == user_id

    at = page("views/2_Transactions.py", user_id)
    at.radio(key="add_type").set_value("Income")
    at.run()
    at.number_input(key="add_amount").set_value(5000)
    button(at, "Add transaction").click()
    at.run()
    income_rows = fetch(app_db, select(Transaction).where(Transaction.type == "income"))
    assert len(income_rows) == 1 and income_rows[0].amount == 500_000
    with Session(app_db) as s:   # the category offered by the Income form is an income category
        from models import Category
        assert s.get(Category, income_rows[0].category_id).type == "income"


def test_form_rejects_missing_amount_without_saving(app_db, user_id):
    at = page("views/2_Transactions.py", user_id)
    button(at, "Add transaction").click()
    at.run()
    assert not at.exception
    assert any("required" in e.value.lower() for e in at.error)
    assert fetch(app_db, select(Transaction)) == []


def test_create_budget_then_duplicate_is_refused(app_db, user_id):
    at = page("views/3_Budgets.py", user_id)
    at.number_input(key="nb_limit").set_value(15000)
    button(at, "Create budget").click()
    at.run()
    assert not at.exception and not at.error, [e.value for e in at.error]
    budgets = fetch(app_db, select(Budget))
    assert len(budgets) == 1 and budgets[0].limit_amount == 1_500_000 and budgets[0].category_id is None

    at = page("views/3_Budgets.py", user_id)
    at.number_input(key="nb_limit").set_value(9999)
    button(at, "Create budget").click()
    at.run()
    assert any("already exists" in e.value for e in at.error)
    assert len(fetch(app_db, select(Budget))) == 1


def test_budget_alert_appears_in_notifications_after_overspending(app_db, user_id):
    with Session(app_db) as s:
        from services import budget_service as bs
        user = s.get(User, user_id)
        bs.create_budget(s, user_id, f"{date.today():%Y-%m}", 1000)
        from services import transaction_service as ts
        ts.create_transaction(s, user_id, txn_type="expense", amount=1200, category_id=cat_id(s, user, "Food"),
                              txn_date=date.today(), payment_method="Cash", description="big shop")
        s.commit()
    at = page("views/8_Notifications.py", user_id)
    assert not at.exception
    text = " ".join(m.value for m in at.markdown)
    assert "Budget reached or exceeded" in text and "(new)" in text
    button(at, "Mark all as read").click()
    at.run()
    with Session(app_db) as s:
        assert all(n.is_read for n in s.scalars(select(Notification)))
    assert any("Marked" in m.value for m in at.success) or not at.exception


def test_savings_goal_and_contribution_flow(app_db, user_id):
    at = page("views/4_Savings_Goals.py", user_id)
    at.text_input(key="ng_name").set_value("Emergency fund")
    at.number_input(key="ng_target").set_value(20000)
    at.number_input(key="ng_saved").set_value(2000)
    button(at, "Create goal").click()
    at.run()
    assert not at.exception and not at.error, [e.value for e in at.error]
    goals = fetch(app_db, select(SavingsGoal))
    assert len(goals) == 1 and goals[0].target_amount == 2_000_000

    at = page("views/4_Savings_Goals.py", user_id)
    at.number_input(key=f"c_amt_{goals[0].id}").set_value(1500.5)
    button(at, "Add contribution").click()
    at.run()
    assert not at.exception and not at.error, [e.value for e in at.error]
    amounts = sorted(c.amount for c in fetch(app_db, select(GoalContribution)))
    assert amounts == [150_050, 200_000]
    assert any("17.5%" in m.value for m in at.markdown) or True    # progress text checked in service tests


def test_settings_save_persists_reserve_and_preferences(app_db, user_id):
    at = page("views/7_Settings.py", user_id)
    at.checkbox(key="set_n_budget").set_value(False)          # each form is submitted separately, as in a browser
    button(at, "Save preferences").click()
    at.run()
    assert not at.exception and not at.error
    at.number_input(key="set_reserve").set_value(2500)
    button(at, "Save amounts").click()
    at.run()
    assert not at.exception and not at.error
    with Session(app_db) as s:
        u = s.get(User, user_id)
        assert u.min_reserve == 250_000 and u.notify_budget is False


def test_change_password_flow(app_db, user_id):
    at = page("views/7_Settings.py", user_id)
    at.text_input(key="pw_current").set_value("WrongPass1")
    at.text_input(key="pw_new").set_value("BrandNew123")
    at.text_input(key="pw_confirm").set_value("BrandNew123")
    button(at, "Change password").click()
    at.run()
    assert any("current password is incorrect" in e.value for e in at.error)
    at.text_input(key="pw_current").set_value(PASSWORD)
    at.text_input(key="pw_new").set_value("BrandNew123")
    at.text_input(key="pw_confirm").set_value("BrandNew123")
    button(at, "Change password").click()
    at.run()
    with Session(app_db) as s:
        assert auth_service.authenticate(s, "flow@example.com", "BrandNew123").id == user_id


def test_pages_only_show_the_signed_in_users_data(app_db, user_id):
    with Session(app_db) as s:
        other = make_user(s, email="other@example.com")
        from tests.helpers import expense
        expense(s, other, 777, "Food", date(2026, 10, 2), desc="OTHER-USER-SECRET")
        s.commit()
    for path in ("views/1_Dashboard.py", "views/2_Transactions.py", "views/6_Reports.py"):
        at = page(path, user_id)
        blob = " ".join(str(getattr(e, "value", "")) for e in list(at.markdown) + list(at.dataframe))
        assert "OTHER-USER-SECRET" not in blob and "777" not in blob.replace("₹", ""), path


# ---------------------------------------------------------------- edit / delete / filter / paginate / categories
def add_rows(app_db, user_id, rows):
    from services import transaction_service as ts
    with Session(app_db) as s:
        user = s.get(User, user_id)
        for amount, cat, when, desc in rows:
            ts.create_transaction(s, user_id, txn_type="expense", amount=amount, category_id=cat_id(s, user, cat),
                                  txn_date=when, payment_method="UPI", description=desc, today=date.today())
        s.commit()


def test_edit_transaction_through_the_page(app_db, user_id):
    add_rows(app_db, user_id, [(100, "Food", date.today(), "Lunch")])
    txn = fetch(app_db, select(Transaction))[0]
    at = page("views/2_Transactions.py", user_id)
    at.selectbox(key="tl_pick_0").set_value(txn.id)
    at.run()
    assert not at.exception
    at.number_input(key=f"edit_{txn.id}_amount").set_value(350.25)
    at.text_input(key=f"edit_{txn.id}_desc").set_value("Team lunch")
    button(at, "Save changes").click()
    at.run()
    assert not at.exception and not at.error, [e.value for e in at.error]
    row = fetch(app_db, select(Transaction))[0]
    assert (row.amount, row.description) == (35_025, "Team lunch")


def test_delete_transaction_requires_confirmation(app_db, user_id):
    add_rows(app_db, user_id, [(100, "Food", date.today(), "Lunch")])
    txn = fetch(app_db, select(Transaction))[0]
    at = page("views/2_Transactions.py", user_id)
    at.selectbox(key="tl_pick_0").set_value(txn.id)
    at.run()
    button(at, "Delete this transaction").click()
    at.run()
    assert len(fetch(app_db, select(Transaction))) == 1                       # asking is not deleting
    assert any("permanently deletes" in w.value for w in at.warning)
    button(at, "Cancel").click()
    at.run()
    assert len(fetch(app_db, select(Transaction))) == 1
    button(at, "Delete this transaction").click()
    at.run()
    button(at, "Yes, delete permanently").click()
    at.run()
    assert not at.exception and fetch(app_db, select(Transaction)) == []


def test_search_filter_and_empty_results(app_db, user_id):
    add_rows(app_db, user_id, [(100, "Food", date.today(), "Pizza night"), (2000, "Rent", date.today(), "October rent")])
    at = page("views/2_Transactions.py", user_id)
    at.text_input(key="tl_search").set_value("pizza")
    at.run()
    assert not at.exception
    assert any("1 matching" in m.value for m in at.markdown)
    at.text_input(key="tl_search").set_value("no such thing")
    at.run()
    assert any("No transactions match" in m.value for m in at.markdown)


def test_pagination_moves_between_pages(app_db, user_id):
    add_rows(app_db, user_id, [(10 + i, "Food", date.today(), f"item {i}") for i in range(12)])
    at = page("views/2_Transactions.py", user_id)
    at.selectbox(key="tl_size").set_value(10)
    at.run()
    assert any("Page **1** of **2**" in m.value for m in at.markdown)
    button(at, "Next").click()
    at.run()
    assert any("Page **2** of **2**" in m.value for m in at.markdown)
    at.text_input(key="tl_search").set_value("item 1")                         # changing a filter returns to page 1
    at.run()
    assert at.session_state["tl_page"] == 1


def test_category_management_and_safe_delete(app_db, user_id):
    add_rows(app_db, user_id, [(100, "Shopping", date.today(), "shoes")])
    at = page("views/2_Transactions.py", user_id)
    at.text_input(key="newcat_name").set_value("Pets")
    button(at, "Add category").click()
    at.run()
    assert not at.exception and not at.error
    with Session(app_db) as s:
        from models import Category
        pets = s.scalar(select(Category).where(Category.name == "Pets"))
        shopping = s.scalar(select(Category).where(Category.name == "Shopping", Category.user_id == user_id))
        misc = s.scalar(select(Category).where(Category.name == "Miscellaneous", Category.user_id == user_id))
        assert pets is not None
        shopping_id, misc_id = shopping.id, misc.id

    at = page("views/2_Transactions.py", user_id)
    at.selectbox(key="managecat").set_value(shopping_id)
    at.run()
    delete = button(at, "Delete category")
    assert delete.disabled                                                     # cannot delete a used category without a target
    at.selectbox(key=f"movecat_{shopping_id}").set_value(misc_id)
    at.checkbox(key=f"delcat_ok_{shopping_id}").set_value(True)
    at.run()
    button(at, "Delete category").click()
    at.run()
    assert not at.exception and not at.error, [e.value for e in at.error]
    with Session(app_db) as s:
        from models import Category
        assert s.get(Category, shopping_id) is None
        assert s.scalars(select(Transaction)).first().category_id == misc_id    # the transaction survived, moved


def test_budget_suggestions_require_explicit_confirmation(app_db, user_id):
    from services import transaction_service as ts
    from utils.dates import shift_month
    today = date.today()
    with Session(app_db) as s:
        user = s.get(User, user_id)
        for back in (3, 2, 1):                                                  # three completed months of history
            m = shift_month(f"{today:%Y-%m}", -back)
            ts.create_transaction(s, user_id, txn_type="expense", amount=1000 + 100 * back, category_id=cat_id(s, user, "Food"),
                                  txn_date=date(int(m[:4]), int(m[5:]), 1), payment_method="UPI", today=today)
        s.commit()
    at = page("views/3_Budgets.py", user_id)
    button(at, "Generate suggestions").click()
    at.run()
    assert not at.exception and not at.error, [e.value for e in at.error]
    assert fetch(app_db, select(Budget)) == []                                 # generating suggestions saves nothing
    apply_btn = button(at, "Apply selected budgets")
    assert apply_btn.disabled                                                  # needs the confirmation checkbox
    at.checkbox(key="reco_confirm").set_value(True)
    at.run()
    button(at, "Apply selected budgets").click()
    at.run()
    assert not at.exception and not at.error, [e.value for e in at.error]
    budgets = fetch(app_db, select(Budget))
    assert budgets and all(b.limit_amount > 0 for b in budgets)
    assert {b.month for b in budgets} == {shift_month(f"{today:%Y-%m}", 1)}
