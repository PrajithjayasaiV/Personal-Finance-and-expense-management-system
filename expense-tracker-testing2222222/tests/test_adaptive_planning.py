from datetime import date

from services import adaptive_planning_service as aps
from tests.helpers import expense, income


def seed_five_months(db, alice):
    months = [(2026, m) for m in range(1, 6)]
    for year, month in months:
        income(db, alice, 40000, "Salary", date(year, month, 1))
        expense(db, alice, 15000, "Rent", date(year, month, 2))
        expense(db, alice, 10000, "Food", date(year, month, 3))
        expense(db, alice, 5000, "Shopping", date(year, month, 4))


def test_five_month_history_and_salary_reduction_plan(db, alice):
    seed_five_months(db, alice)
    history = aps.five_month_history(db, alice.id, "2026-06")

    assert history.income_months_recorded == 5
    assert history.average_income == 4_000_000
    assert history.expense_months_recorded == 5
    plan = aps.make_salary_adjustment_plan(history, 3_000_000, reserve=200_000, commitments=300_000)

    assert plan.income_decreased
    assert plan.income_change_pct == -25.0
    assert plan.essential_average == 2_500_000
    assert plan.flexible_average == 500_000
    assert plan.essential_shortfall == 0
    assert plan.total_reduction == 500_000
    assert plan.recommended_expenses == 2_500_000


def test_auto_budget_unlocks_after_five_distinct_expense_months(db, alice):
    seed_five_months(db, alice)
    recommendation = aps.auto_budget_recommendations(db, alice.id, "2026-06")

    assert recommendation.ready
    assert len(recommendation.basis_months) == 5
    by_name = {item.name: item.average_monthly for item in recommendation.items}
    assert by_name["Food"] == 1_000_000
    assert by_name["Rent"] == 1_500_000
    assert recommendation.suggested_total == 3_000_000


def test_auto_budget_stays_locked_with_only_four_expense_months(db, alice):
    for month in range(1, 5):
        expense(db, alice, 1000, "Food", date(2026, month, 3))
    recommendation = aps.auto_budget_recommendations(db, alice.id, "2026-05")
    assert not recommendation.ready
    assert recommendation.items == ()


def test_possible_income_drop_requires_five_baseline_income_months(db, alice):
    seed_five_months(db, alice)
    history = aps.five_month_history(db, alice.id, "2026-06")
    assert aps.possible_income_drop(history, 3_000_000) == 25.0
    assert aps.possible_income_drop(history, 3_900_000) is None
