"""Exact-figure tests for the financial formulas."""
from datetime import date

import pytest

from services import analytics_service as an
from services import transaction_service as ts
from tests.helpers import TODAY, cat_id, expense, income, make_user
from utils import dates
from utils.formatting import format_money
from utils.money import paise_to_decimal, percentage, to_paise


def test_spec_example_balance_and_net_cash_flow(db):
    user = make_user(db, opening=1000)
    income(db, user, 5000, "Salary", date(2026, 10, 1))
    expense(db, user, 2000, "Rent", date(2026, 10, 2))
    assert an.current_balance(db, user.id) == 400_000                 # 1,000 + 5,000 - 2,000
    totals = an.period_totals(db, user.id, date(2026, 10, 1), date(2026, 10, 31))
    assert (totals.income, totals.expense, totals.net) == (500_000, 200_000, 300_000)
    assert totals.savings_rate == 60.0


def test_opening_balance_counted_once_and_is_not_income(db):
    user = make_user(db, opening=1000)
    assert an.current_balance(db, user.id) == 100_000
    assert an.period_totals(db, user.id).income == 0
    income(db, user, 10, "Gift", TODAY)
    income(db, user, 10, "Gift", TODAY)
    assert an.current_balance(db, user.id) == 102_000


def test_paise_precision_has_no_float_drift(db):
    user = make_user(db)
    for _ in range(10):
        income(db, user, 0.1, "Gift", TODAY)
    expense(db, user, 0.3, "Food", TODAY)
    assert an.period_totals(db, user.id).income == 100
    assert an.current_balance(db, user.id) == 70                      # exactly Rs 0.70
    assert paise_to_decimal(an.current_balance(db, user.id)) == paise_to_decimal(70)


def test_decimal_rupee_amounts(db):
    user = make_user(db, opening=0.05)
    income(db, user, 1250.50, "Freelance Income", TODAY)
    expense(db, user, 333.33, "Food", TODAY)
    assert an.current_balance(db, user.id) == 5 + 125_050 - 33_333


def test_empty_account_is_safe(db):
    user = make_user(db)
    totals = an.period_totals(db, user.id, date(2026, 10, 1), date(2026, 10, 31))
    assert (totals.income, totals.expense, totals.net, totals.savings_rate) == (0, 0, 0, None)
    assert an.current_balance(db, user.id) == 0
    assert an.category_totals(db, user.id, date(2026, 10, 1), date(2026, 10, 31)) == []
    assert an.first_transaction_date(db, user.id) is None


def test_zero_income_and_negative_cash_flow(db):
    user = make_user(db)
    expense(db, user, 500, "Food", TODAY)
    totals = an.period_totals(db, user.id)
    assert totals.savings_rate is None and totals.net == -50_000
    income(db, user, 200, "Gift", TODAY)
    totals = an.period_totals(db, user.id)
    assert totals.net == -30_000 and totals.savings_rate == -150.0


def test_savings_rate_rounding():
    assert an.savings_rate(300_000, 100_000) == 66.67
    assert an.savings_rate(0, 100) is None
    assert an.savings_rate(100, 100) == 0.0


def test_editing_and_deleting_past_transactions_keep_everything_consistent(db):
    user = make_user(db, opening=1000)
    old = expense(db, user, 400, "Food", date(2026, 8, 5))
    income(db, user, 3000, "Salary", date(2026, 9, 1))
    assert an.current_balance(db, user.id) == 360_000
    ts.update_transaction(db, user.id, old.id, txn_type="expense", amount=150, category_id=cat_id(db, user, "Food"),
                          txn_date=date(2026, 8, 5), payment_method="Cash", today=TODAY)
    assert an.current_balance(db, user.id) == 385_000
    assert an.period_totals(db, user.id, date(2026, 8, 1), date(2026, 8, 31)).expense == 15_000
    ts.delete_transaction(db, user.id, old.id)
    assert an.current_balance(db, user.id) == 400_000
    assert an.period_totals(db, user.id, date(2026, 8, 1), date(2026, 8, 31)).expense == 0


def test_date_range_is_inclusive(db):
    user = make_user(db)
    expense(db, user, 10, "Food", date(2026, 9, 1))
    expense(db, user, 20, "Food", date(2026, 9, 30))
    expense(db, user, 40, "Food", date(2026, 10, 1))
    assert an.period_totals(db, user.id, date(2026, 9, 1), date(2026, 9, 30)).expense == 3000


def test_to_paise_edge_cases():
    assert to_paise("1,25,000.50") == 12_500_050
    assert to_paise("₹99") == 9_900
    assert to_paise(0, allow_zero=True) == 0
    assert percentage(1, 3) == 33.33 and percentage(2, 3) == 66.67
    assert percentage(5, 0) is None and percentage(0, 10) == 0.0


def test_indian_money_formatting():
    assert format_money(12_505_000) == "₹1,25,050.00"
    assert format_money(150_000) == "₹1,500.00"
    assert format_money(-125_050) == "-₹1,250.50"
    assert format_money(0) == "₹0.00"
    assert format_money(100_000_000) == "₹10,00,000.00"
    assert format_money(123_456_789, "USD") == "$1,234,567.89"
    assert format_money(5_000, signed=True) == "+₹50.00"
    assert format_money(None) == "—"


def test_date_helpers():
    assert dates.shift_month("2026-01", -1) == "2025-12"
    assert dates.shift_month("2026-12", 1) == "2027-01"
    assert dates.month_bounds("2024-02") == (date(2024, 2, 1), date(2024, 2, 29))
    assert dates.add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert dates.resolve_preset("previous_month", date(2026, 1, 15)) == (date(2025, 12, 1), date(2025, 12, 31))
    assert dates.resolve_preset("last_30_days", date(2026, 10, 24)) == (date(2026, 9, 25), date(2026, 10, 24))
    assert dates.resolve_preset("all_time", TODAY) == (None, None)
    with pytest.raises(Exception):
        dates.parse_month("2026-13")


def test_previous_comparable_period():
    # month-to-date compares the same number of days of the previous month
    assert dates.previous_comparable_period(date(2026, 10, 1), date(2026, 10, 24)) == (date(2026, 9, 1), date(2026, 9, 24))
    # a month shorter than the elapsed day count is capped at its last day
    assert dates.previous_comparable_period(date(2026, 3, 1), date(2026, 3, 31)) == (date(2026, 2, 1), date(2026, 2, 28))
    assert dates.previous_comparable_period(date(2026, 3, 1), date(2026, 3, 30)) == (date(2026, 2, 1), date(2026, 2, 28))
    # full past month -> full previous month
    assert dates.previous_comparable_period(date(2026, 9, 1), date(2026, 9, 30)) == (date(2026, 8, 1), date(2026, 8, 31))
    # arbitrary window -> the window immediately before, same length
    assert dates.previous_comparable_period(date(2026, 10, 10), date(2026, 10, 19)) == (date(2026, 9, 30), date(2026, 10, 9))
