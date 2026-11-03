from datetime import date, timedelta

import pytest

from models import Transaction
from services import transaction_service as ts
from tests.helpers import TODAY, cat_id, expense, income
from utils.errors import AuthorizationError, ConflictError, NotFoundError, ValidationError


def create(db, user, **overrides):
    args = dict(txn_type="expense", amount=100, category_id=cat_id(db, user, "Food"), txn_date=TODAY,
                payment_method="Cash", description="Lunch", today=TODAY)
    args.update(overrides)
    return ts.create_transaction(db, user.id, **args)


def test_create_income_and_expense(db, alice):
    inc = income(db, alice, 50000, "Salary", date(2026, 10, 1), desc="October pay")
    exp = expense(db, alice, 249.99, "Food", date(2026, 10, 2))
    assert inc.amount == 5_000_000 and inc.type == "income"
    assert exp.amount == 24_999 and exp.type == "expense"
    assert exp.user_id == alice.id


@pytest.mark.parametrize("amount", [0, -5, -0.01, "abc", None, float("nan"), float("inf"), 10**13])
def test_invalid_amounts_rejected(db, alice, amount):
    with pytest.raises(ValidationError):
        create(db, alice, amount=amount)


def test_amount_rounds_to_two_places_half_up(db, alice):
    assert create(db, alice, amount="10.005").amount == 1001
    assert create(db, alice, amount=0.1 + 0.2).amount == 30     # float noise never reaches the database


def test_invalid_dates_rejected(db, alice):
    with pytest.raises(ValidationError):
        create(db, alice, txn_date=None)
    with pytest.raises(ValidationError):
        create(db, alice, txn_date="2026-10-01")
    with pytest.raises(ValidationError, match="future"):
        create(db, alice, txn_date=TODAY + timedelta(days=1))


def test_invalid_category_and_payment_method(db, alice):
    with pytest.raises(ValidationError):
        create(db, alice, category_id=None)
    with pytest.raises(ValidationError):
        create(db, alice, category_id=987654)
    with pytest.raises(ValidationError):
        create(db, alice, payment_method="Bitcoin")
    with pytest.raises(ValidationError):
        create(db, alice, txn_type="transfer")


def test_category_must_match_type(db, alice):
    with pytest.raises(ValidationError, match="income category"):
        create(db, alice, txn_type="income", category_id=cat_id(db, alice, "Food"))
    with pytest.raises(ValidationError, match="expense category"):
        create(db, alice, txn_type="expense", category_id=cat_id(db, alice, "Salary", "income"))


def test_other_users_category_rejected(db, alice, bob):
    with pytest.raises(AuthorizationError):
        create(db, alice, category_id=cat_id(db, bob, "Food"))


def test_database_itself_rejects_cross_user_category(db, alice, bob):
    """Defence in depth: the composite foreign key blocks it even if the service layer were bypassed."""
    from sqlalchemy.exc import IntegrityError
    db.add(Transaction(user_id=alice.id, category_id=cat_id(db, bob, "Food"), type="expense", amount=100,
                       txn_date=TODAY, payment_method="Cash", description=""))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_edit_transaction(db, alice):
    txn = create(db, alice, amount=100)
    ts.update_transaction(db, alice.id, txn.id, txn_type="expense", amount=450.25,
                          category_id=cat_id(db, alice, "Groceries"), txn_date=date(2026, 10, 3),
                          payment_method="UPI", description="Weekly shop", today=TODAY)
    row = ts.get_transaction(db, alice.id, txn.id)
    assert (row.amount, row.payment_method, row.description, row.txn_date) == (45_025, "UPI", "Weekly shop", date(2026, 10, 3))
    assert row.category_id == cat_id(db, alice, "Groceries")


def test_edit_can_switch_type_with_matching_category(db, alice):
    txn = create(db, alice)
    ts.update_transaction(db, alice.id, txn.id, txn_type="income", amount=100, category_id=cat_id(db, alice, "Gift", "income"),
                          txn_date=TODAY, payment_method="Cash", today=TODAY)
    assert ts.get_transaction(db, alice.id, txn.id).type == "income"
    with pytest.raises(ValidationError):
        ts.update_transaction(db, alice.id, txn.id, txn_type="income", amount=100, category_id=cat_id(db, alice, "Food"),
                              txn_date=TODAY, payment_method="Cash", today=TODAY)


def test_delete_transaction(db, alice):
    txn = create(db, alice)
    ts.delete_transaction(db, alice.id, txn.id)
    with pytest.raises(NotFoundError):
        ts.get_transaction(db, alice.id, txn.id)
    assert ts.count_transactions(db, alice.id) == 0


def _seed(db, user):
    expense(db, user, 100, "Food", date(2026, 10, 1), method="Cash", desc="Breakfast at cafe")
    expense(db, user, 2500, "Rent", date(2026, 10, 2), method="Bank Transfer", desc="October rent")
    expense(db, user, 300, "Food", date(2026, 9, 28), method="UPI", desc="Dinner_out 50% off")
    income(db, user, 40000, "Salary", date(2026, 10, 1), method="Bank Transfer", desc="Pay")
    expense(db, user, 75, "Transportation", date(2026, 10, 5), method="UPI", desc="Metro")


def test_filters_by_type_category_method_and_dates(db, alice):
    _seed(db, alice)
    q = lambda **kw: ts.query_transactions(db, alice.id, ts.TransactionFilters(**kw))
    assert q(type="income")[1] == 1
    assert q(type="expense")[1] == 4
    assert q(category_id=cat_id(db, alice, "Food"))[1] == 2
    assert q(payment_method="UPI")[1] == 2
    rows, total = q(start=date(2026, 10, 1), end=date(2026, 10, 2))
    assert total == 3 and all(date(2026, 10, 1) <= r.txn_date <= date(2026, 10, 2) for r in rows)
    assert q(start=date(2027, 1, 1))[1] == 0
    with pytest.raises(ValidationError):
        q(start=date(2026, 10, 5), end=date(2026, 10, 1))


def test_search_description_and_category_with_literal_wildcards(db, alice):
    _seed(db, alice)
    q = lambda s: ts.query_transactions(db, alice.id, ts.TransactionFilters(search=s))[1]
    assert q("cafe") == 1
    assert q("RENT") == 1            # case-insensitive; matches description and category name
    assert q("transport") == 1       # category name
    assert q("50%") == 1             # % is literal, not a wildcard
    assert q("_") == 1               # _ is literal too
    assert q("zzz") == 0


def test_sorting_and_pagination(db, alice):
    _seed(db, alice)
    f = ts.TransactionFilters()
    by_date, _ = ts.query_transactions(db, alice.id, f, sort_by="date", descending=True)
    assert [r.txn_date for r in by_date] == sorted((r.txn_date for r in by_date), reverse=True)
    by_amount, _ = ts.query_transactions(db, alice.id, f, sort_by="amount", descending=True)
    assert by_amount[0].amount == 4_000_000
    asc, _ = ts.query_transactions(db, alice.id, f, sort_by="amount", descending=False)
    assert asc[0].amount == 7_500
    page1, total = ts.query_transactions(db, alice.id, f, limit=2, offset=0)
    page3, _ = ts.query_transactions(db, alice.id, f, limit=2, offset=4)
    assert total == 5 and len(page1) == 2 and len(page3) == 1
    assert {r.id for r in page1}.isdisjoint({r.id for r in page3})


def test_default_categories_are_seeded_once(db, alice):
    before = len(ts.list_categories(db, alice.id))
    ts.seed_default_categories(db, alice.id)
    assert len(ts.list_categories(db, alice.id)) == before


def test_create_and_rename_custom_category(db, alice):
    cat = ts.create_category(db, alice.id, "  Pet   care ", "expense")
    assert cat.name == "Pet care" and not cat.is_default
    with pytest.raises(ConflictError):
        ts.create_category(db, alice.id, "PET CARE", "expense")        # case-insensitive duplicate
    assert ts.create_category(db, alice.id, "Pet care", "income")       # same name is fine for the other type
    ts.rename_category(db, alice.id, cat.id, "Pets")
    assert ts.get_category(db, alice.id, cat.id).name == "Pets"
    with pytest.raises(ConflictError):
        ts.rename_category(db, alice.id, cat.id, "food")
    with pytest.raises(ValidationError):
        ts.create_category(db, alice.id, " ", "expense")


def test_used_category_cannot_be_deleted_without_reassigning(db, alice):
    txn = expense(db, alice, 100, "Shopping", TODAY)
    shopping = cat_id(db, alice, "Shopping")
    with pytest.raises(ValidationError, match="Choose another category"):
        ts.delete_category(db, alice.id, shopping)
    assert ts.get_transaction(db, alice.id, txn.id)                      # nothing was erased
    with pytest.raises(ValidationError):
        ts.delete_category(db, alice.id, shopping, reassign_to_id=cat_id(db, alice, "Salary", "income"))  # wrong type
    with pytest.raises(ValidationError):
        ts.delete_category(db, alice.id, shopping, reassign_to_id=shopping)
    ts.delete_category(db, alice.id, shopping, reassign_to_id=cat_id(db, alice, "Miscellaneous"))
    db.refresh(txn)
    assert txn.category_id == cat_id(db, alice, "Miscellaneous")
    assert cat_id(db, alice, "Shopping") is None


def test_unused_category_can_be_deleted(db, alice):
    cat = ts.create_category(db, alice.id, "Temp", "expense")
    ts.delete_category(db, alice.id, cat.id)
    with pytest.raises(NotFoundError):
        ts.get_category(db, alice.id, cat.id)


def test_usage_counts(db, alice):
    expense(db, alice, 10, "Food", TODAY)
    expense(db, alice, 10, "Food", TODAY)
    assert ts.category_usage_counts(db, alice.id)[cat_id(db, alice, "Food")] == 2
