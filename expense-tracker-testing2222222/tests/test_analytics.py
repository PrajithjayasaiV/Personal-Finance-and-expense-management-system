from datetime import date

import pytest

from services import analytics_service as an
from tests.helpers import expense, income, make_user
from utils.errors import ValidationError


@pytest.fixture()
def user(db):
    u = make_user(db, opening=1000)
    income(db, u, 40000, "Salary", date(2026, 9, 1))
    income(db, u, 42000, "Salary", date(2026, 10, 1))
    expense(db, u, 6000, "Rent", date(2026, 9, 3), method="Bank Transfer")
    expense(db, u, 1500, "Food", date(2026, 9, 10), method="UPI")
    expense(db, u, 2000, "Food", date(2026, 10, 5), method="UPI")
    expense(db, u, 500, "Transportation", date(2026, 10, 6), method="Cash")
    expense(db, u, 6000, "Rent", date(2026, 10, 3), method="Bank Transfer")
    return u


def test_category_totals_sorted_with_shares(db, user):
    rows = an.category_totals(db, user.id, date(2026, 10, 1), date(2026, 10, 31))
    assert [(r.name, r.total) for r in rows] == [("Rent", 600_000), ("Food", 200_000), ("Transportation", 50_000)]
    assert [r.share_pct for r in rows] == [70.59, 23.53, 5.88]
    assert sum(r.total for r in rows) == an.period_totals(db, user.id, date(2026, 10, 1), date(2026, 10, 31)).expense


def test_income_categories_excluded_from_expense_breakdown(db, user):
    names = {r.name for r in an.category_totals(db, user.id, None, None)}
    assert "Salary" not in names
    assert [r.name for r in an.category_totals(db, user.id, None, None, "income")] == ["Salary"]


def test_monthly_series_matches_totals_and_zero_fills(db, user):
    pts = an.monthly_series(db, user.id, "2026-08", "2026-10")
    assert [(p.period_start, p.income, p.expense) for p in pts] == [
        (date(2026, 8, 1), 0, 0), (date(2026, 9, 1), 4_000_000, 750_000), (date(2026, 10, 1), 4_200_000, 850_000)]
    assert pts[1].net == 3_250_000


def test_series_granularities(db, user):
    daily = an.series(db, user.id, date(2026, 10, 1), date(2026, 10, 7), "day")
    assert len(daily) == 7 and sum(p.expense for p in daily) == 850_000
    weekly = an.series(db, user.id, date(2026, 10, 1), date(2026, 10, 14), "week")
    assert [p.period_start for p in weekly] == [date(2026, 9, 28), date(2026, 10, 5), date(2026, 10, 12)]
    assert weekly[0].expense == 600_000 and weekly[1].expense == 250_000      # Oct 3 rent | Oct 5-6
    with pytest.raises(ValidationError):
        an.series(db, user.id, date(2026, 10, 1), date(2026, 10, 7), "year")
    with pytest.raises(ValidationError):
        an.series(db, user.id, date(2026, 10, 7), date(2026, 10, 1), "day")
    assert an.auto_granularity(date(2026, 10, 1), date(2026, 10, 31)) == "day"
    assert an.auto_granularity(date(2026, 1, 1), date(2026, 6, 1)) == "week"
    assert an.auto_granularity(date(2026, 1, 1), date(2026, 12, 31)) == "month"


def test_period_comparison_and_missing_baseline(db, user):
    cmp = an.compare(200_000, 150_000)
    assert (cmp.change, cmp.change_pct, cmp.available) == (50_000, 33.33, True)
    assert an.compare(100_000, 150_000).change_pct == -33.33
    none = an.compare(10_000, 0)
    assert none.change_pct is None and not none.available and "percentage" in none.reason
    rows = dict(an.compare_categories(db, user.id, date(2026, 10, 1), date(2026, 10, 24), date(2026, 9, 1), date(2026, 9, 24)))
    assert rows["Food"].change == 50_000 and rows["Food"].change_pct == 33.33
    assert rows["Transportation"].available is False
    assert rows["Rent"].change == 0


def test_payment_method_distribution(db, user):
    rows = an.payment_method_totals(db, user.id, date(2026, 10, 1), date(2026, 10, 31), "expense")
    assert rows == [("Bank Transfer", 1, 600_000), ("UPI", 1, 200_000), ("Cash", 1, 50_000)]
    assert an.payment_method_totals(db, user.id, date(2027, 1, 1), date(2027, 1, 31)) == []


def test_average_monthly_category_spending(db, user):
    rows = dict((n, avg) for n, avg, _ in an.average_monthly_category_spending(db, user.id, date(2026, 9, 1), date(2026, 10, 31)))
    assert rows["Rent"] == 600_000 and rows["Food"] == 175_000        # (1500 + 2000) / 2 months


def test_monthly_category_spending_map(db, user):
    spend = an.monthly_category_spending(db, user.id, "2026-09", "2026-10")
    by_name = {sum(v.values()) for v in spend.values()}
    assert 1_200_000 in by_name and 350_000 in by_name
