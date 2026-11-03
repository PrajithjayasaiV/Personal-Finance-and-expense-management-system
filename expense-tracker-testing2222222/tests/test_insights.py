from datetime import date, timedelta

import pytest
from sqlalchemy import select

from models import Notification
from services import auth_service
from services import budget_service as bs
from services import insights_service as ins
from services import notification_service as ns
from services import savings_service as sv
from tests.helpers import TODAY, cat_id, expense, income, make_user

START = date(2026, 10, 1)


def run(db, user, **kw):
    return ins.generate_insights(db, user.id, START, TODAY, today=TODAY, **kw)


def of_kind(items, kind):
    return [i for i in items if i.kind == kind]


# ------------------------------------------------------------------ comparisons
def test_category_increase_matches_spec_example(db, alice):
    expense(db, alice, 1500, "Food", date(2026, 9, 3))
    expense(db, alice, 2000, "Food", date(2026, 10, 5))
    [insight] = of_kind(run(db, alice), "category_increase")
    assert "from ₹1,500.00 to ₹2,000.00" in insight.message
    assert "₹500.00" in insight.message and "33.33%" in insight.message
    assert "01-09-2026" in insight.evidence and "24-09-2026" in insight.evidence   # previous comparable period is shown
    assert insight.notify is False and insight.severity == "info"


def test_large_jump_is_an_unusual_increase_without_accusations(db, alice):
    expense(db, alice, 1000, "Food", date(2026, 9, 3))
    expense(db, alice, 2500, "Food", date(2026, 10, 5))
    [insight] = of_kind(run(db, alice), "unusual_increase")
    assert insight.notify and insight.severity == "warning"
    text = (insight.title + insight.message).lower()
    assert "fraud" not in text and "unnecessary" not in text and "wasteful" not in text


def test_decrease_insights(db, alice):
    expense(db, alice, 3000, "Food", date(2026, 9, 3))
    expense(db, alice, 1000, "Food", date(2026, 10, 5))
    items = run(db, alice)
    assert of_kind(items, "category_decrease") and of_kind(items, "spending_change")
    assert "lower" in of_kind(items, "spending_change")[0].title.lower()


def test_no_comparison_insights_without_valid_baseline(db, alice):
    assert run(db, alice) == []                                       # no data at all
    expense(db, alice, 2000, "Food", date(2026, 10, 5))
    assert run(db, alice) == []                                       # nothing in the previous period
    small = make_user(db, "Small", "small@example.com")
    expense(db, small, 300, "Food", date(2026, 9, 3))                 # baseline below the Rs 500 minimum
    expense(db, small, 2000, "Food", date(2026, 10, 5))
    assert of_kind(run(db, small), "category_increase") == [] and of_kind(run(db, small), "unusual_increase") == []


def test_partial_previous_period_is_not_compared(db, alice):
    """Started recording on 20 Sep: September is not fully observed, so no increase is claimed."""
    expense(db, alice, 1500, "Food", date(2026, 9, 20))
    expense(db, alice, 4000, "Food", date(2026, 10, 5))
    items = run(db, alice)
    assert not [i for i in items if i.kind in ("category_increase", "unusual_increase", "spending_change")]


def test_small_changes_are_ignored(db, alice):
    expense(db, alice, 1500, "Food", date(2026, 9, 3))
    expense(db, alice, 1600, "Food", date(2026, 10, 5))               # +Rs 100 / 6.7%
    assert not [i for i in run(db, alice) if i.kind.startswith("category") or i.kind == "spending_change"]


# ------------------------------------------------------------------ other rules
def test_top_category_share(db, alice):
    expense(db, alice, 5000, "Rent", date(2026, 10, 2))
    expense(db, alice, 1000, "Food", date(2026, 10, 3))
    expense(db, alice, 500, "Transportation", date(2026, 10, 4))
    [top] = of_kind(run(db, alice), "top_category")
    assert "Rent" in top.title and "76.9%" in top.message
    assert "₹5,000.00 of ₹6,500.00" in top.evidence


def test_top_category_needs_enough_records_and_share(db, alice):
    expense(db, alice, 5000, "Rent", date(2026, 10, 2))
    expense(db, alice, 1000, "Food", date(2026, 10, 3))
    assert of_kind(run(db, alice), "top_category") == []              # only 2 expense records
    for cat in ("Food", "Shopping", "Utilities", "Travel"):
        expense(db, alice, 4900, cat, date(2026, 10, 4))
    assert of_kind(run(db, alice), "top_category") == []              # largest share is below 25%


def test_negative_cash_flow(db, alice):
    income(db, alice, 1000, "Salary", date(2026, 10, 1))
    expense(db, alice, 1800, "Rent", date(2026, 10, 2))
    [flow] = of_kind(run(db, alice), "negative_cash_flow")
    assert "₹800.00" in flow.message and "-80.0%" in flow.message


def test_budget_alert_insights_sorted_first(db, alice):
    bs.create_budget(db, alice.id, "2026-10", 1000)
    bs.create_budget(db, alice.id, "2026-10", 400, cat_id(db, alice, "Food"))
    expense(db, alice, 1100, "Food", date(2026, 10, 5))
    items = run(db, alice)
    assert items[0].kind == "budget_exceeded" and items[0].severity == "critical"
    assert "Overall" in items[0].title or "overall" in items[0].message
    assert len(of_kind(items, "budget_exceeded")) == 2


def test_repeated_overspending_needs_three_budgeted_months(db, alice):
    food = cat_id(db, alice, "Food")
    for month in ("2026-09", "2026-10"):
        bs.create_budget(db, alice.id, month, 1000, food)
    expense(db, alice, 1500, "Food", date(2026, 9, 5))
    expense(db, alice, 1500, "Food", date(2026, 10, 5))
    assert of_kind(run(db, alice), "repeated_overspending") == []     # only two budgeted months: not enough history
    bs.create_budget(db, alice.id, "2026-08", 1000, food)
    expense(db, alice, 800, "Food", date(2026, 8, 5))
    [rep] = of_kind(run(db, alice), "repeated_overspending")
    assert "2 of your last 3" in rep.message and rep.notify
    assert "Sep 2026" in rep.message and "Oct 2026" in rep.message


def test_repeated_overspending_not_flagged_when_only_once(db, alice):
    food = cat_id(db, alice, "Food")
    for month, spend in (("2026-08", 500), ("2026-09", 1500), ("2026-10", 600)):
        bs.create_budget(db, alice.id, month, 1000, food)
        expense(db, alice, spend, "Food", date(int(month[:4]), int(month[5:]), 5))
    assert of_kind(run(db, alice), "repeated_overspending") == []


def test_goal_behind_insight(db, alice):
    expense(db, alice, 100, "Food", date(2026, 10, 2))                # gives the user some history
    g = sv.create_goal(db, alice.id, name="Laptop", target_amount=60000, start_date=TODAY - timedelta(days=60),
                       target_date=TODAY + timedelta(days=90), today=TODAY)
    sv.add_contribution(db, alice.id, g.id, 1000, TODAY - timedelta(days=3), today=TODAY)
    [goal] = of_kind(run(db, alice), "goal_behind")
    assert "Laptop" in goal.message and "per month" in goal.message and "estimates" in goal.evidence


def test_dismiss_acknowledge_and_restore(db, alice):
    expense(db, alice, 1500, "Food", date(2026, 9, 3))
    expense(db, alice, 2000, "Food", date(2026, 10, 5))
    first = of_kind(run(db, alice), "category_increase")[0]
    ins.set_feedback(db, alice.id, first.key, "dismissed")
    assert first.key not in {i.key for i in run(db, alice)}
    shown = {i.key: i for i in run(db, alice, include_hidden=True)}
    assert shown[first.key].hidden is True
    ins.set_feedback(db, alice.id, first.key, "acknowledged")         # changing state keeps a single row
    assert ins.clear_feedback(db, alice.id) == 1
    assert first.key in {i.key for i in run(db, alice)}
    with pytest.raises(Exception):
        ins.set_feedback(db, alice.id, first.key, "bogus")


def test_insight_key_is_stable_across_days_in_the_same_month(db, alice):
    expense(db, alice, 1500, "Food", date(2026, 9, 3))
    expense(db, alice, 2000, "Food", date(2026, 10, 5))
    a = of_kind(ins.generate_insights(db, alice.id, START, date(2026, 10, 24), today=date(2026, 10, 24)), "category_increase")[0]
    b = of_kind(ins.generate_insights(db, alice.id, START, date(2026, 10, 25), today=date(2026, 10, 25)), "category_increase")[0]
    assert a.key == b.key


def test_limit_and_order(db, alice):
    expense(db, alice, 1000, "Food", date(2026, 9, 3))
    expense(db, alice, 2500, "Food", date(2026, 10, 5))
    bs.create_budget(db, alice.id, "2026-10", 1000)
    items = run(db, alice, limit=2)
    assert len(items) == 2 and items[0].priority >= items[1].priority


def test_insight_notifications_do_not_duplicate(db, alice):
    expense(db, alice, 1000, "Food", date(2026, 9, 3))
    expense(db, alice, 2500, "Food", date(2026, 10, 5))
    for _ in range(3):
        ns.sync_insight_notifications(db, alice.id, TODAY)
    rows = db.scalars(select(Notification).where(Notification.type == "insight")).all()
    assert len(rows) == 1
    auth_service.update_settings(db, alice.id, notify_insights=False)
    other = make_user(db, "Opt Out", "optout@example.com")
    auth_service.update_settings(db, other.id, notify_insights=False)
    expense(db, other, 1000, "Food", date(2026, 9, 3))
    expense(db, other, 2500, "Food", date(2026, 10, 5))
    ns.sync_insight_notifications(db, other.id, TODAY)
    assert db.scalars(select(Notification).where(Notification.user_id == other.id)).all() == []


# ------------------------------------------------------------------ Safe-to-Spend
def configure(db, user, reserve=0, commitments=0):
    auth_service.update_settings(db, user.id, min_reserve=reserve, upcoming_commitments=commitments)


def test_safe_to_spend_spec_example(db):
    user = make_user(db, opening=8000)
    configure(db, user, reserve=2000)
    bs.create_budget(db, user.id, "2026-10", 3000)
    s = ins.safe_to_spend(db, user.id, TODAY)
    assert (s.balance, s.reserve, s.available, s.remaining_budget, s.estimate) == (800_000, 200_000, 600_000, 300_000, 300_000)
    assert s.basis == "budget_limited"
    assert any("budget limits the estimate" in line for line in s.explanation)


def test_safe_to_spend_limited_by_balance(db):
    user = make_user(db, opening=8000)
    configure(db, user, reserve=2000)
    bs.create_budget(db, user.id, "2026-10", 10000)
    s = ins.safe_to_spend(db, user.id, TODAY)
    assert s.estimate == 600_000 and s.basis == "balance_limited"


def test_safe_to_spend_without_budget_is_labelled(db):
    user = make_user(db, opening=8000)
    configure(db, user, reserve=2000)
    s = ins.safe_to_spend(db, user.id, TODAY)
    assert s.estimate == 600_000 and s.basis == "balance_only" and s.monthly_budget is None
    assert any("not checked against a monthly budget" in line for line in s.explanation)


def test_safe_to_spend_never_negative_and_flags_overspending(db):
    user = make_user(db)
    expense(db, user, 500, "Food", date(2026, 10, 2))
    s = ins.safe_to_spend(db, user.id, TODAY)
    assert s.balance == -50_000 and s.estimate == 0 and any("below zero" in w for w in s.warnings)
    rich = make_user(db, "Rich", "rich@example.com", opening=10000)
    bs.create_budget(db, rich.id, "2026-10", 1000)
    expense(db, rich, 1500, "Food", date(2026, 10, 2))
    s = ins.safe_to_spend(db, rich.id, TODAY)
    assert s.remaining_budget == -50_000 and s.estimate == 0 and any("over this month" in w for w in s.warnings)


def test_safe_to_spend_does_not_double_count_recorded_expenses(db):
    user = make_user(db, opening=10000)
    bs.create_budget(db, user.id, "2026-10", 5000)
    expense(db, user, 2000, "Food", date(2026, 10, 2))               # already reflected in the balance
    s = ins.safe_to_spend(db, user.id, TODAY)
    assert s.balance == 800_000 and s.remaining_budget == 300_000 and s.estimate == 300_000


def test_safe_to_spend_commitments_and_reserve_floor(db):
    user = make_user(db, opening=8000)
    configure(db, user, reserve=2000, commitments=1000)
    assert ins.safe_to_spend(db, user.id, TODAY).available == 500_000
    configure(db, user, reserve=9000, commitments=0)
    assert ins.safe_to_spend(db, user.id, TODAY).estimate == 0        # reserve above balance -> zero, not negative


# ------------------------------------------------------------------ budget recommendations
def history(db, user, rows):
    """rows: {category: (jul, aug, sep)} -> one expense per month on the 1st (so recording starts on 1 July)."""
    for name, amounts in rows.items():
        for month, amount in zip((7, 8, 9), amounts):
            if amount:
                expense(db, user, amount, name, date(2026, month, 1))


def test_recommendation_uses_weighted_history_and_explains_itself(db, alice):
    history(db, alice, {"Food": (1000, 1200, 1500)})
    rec = ins.recommend_budgets(db, alice.id, "2026-11", today=TODAY)
    food = next(i for i in rec.items if i.category_name == "Food")
    assert food.weighted_average == 131_667            # (1*1000 + 2*1200 + 3*1500) / 6
    assert food.buffer_pct == 5 and food.suggested == 139_000 and food.confidence == "high"
    assert [m for m, _ in food.history] == ["Jul 2026", "Aug 2026", "Sep 2026"]
    assert "weighted average" in food.explanation and "5% buffer" in food.explanation
    assert rec.confidence == "high" and not rec.is_starting_estimate and len(rec.basis_months) == 3
    assert any("not saved" in a for a in rec.assumptions)
    assert bs.list_budgets(db, alice.id, "2026-11") == []   # nothing is saved by generating suggestions


def test_recommendation_with_sparse_history_is_a_starting_estimate(db, alice):
    expense(db, alice, 1000, "Food", date(2026, 9, 2))
    rec = ins.recommend_budgets(db, alice.id, "2026-11", today=TODAY)
    assert rec.is_starting_estimate and rec.confidence == "low" and rec.basis_months == ["2026-09"]
    food = rec.items[0]
    assert food.buffer_pct == 10 and food.suggested == 110_000 and food.confidence == "low"
    assert any("Starting estimate" in n for n in rec.notes) and any("before you started" in n for n in rec.notes)


def test_recommendation_projects_current_month_when_no_completed_month(db, alice):
    expense(db, alice, 1000, "Food", date(2026, 10, 5))
    rec = ins.recommend_budgets(db, alice.id, "2026-11", today=TODAY)
    assert rec.is_starting_estimate and "projected" in rec.basis_label
    assert rec.items[0].weighted_average == 129_167 and rec.items[0].suggested == 143_000


def test_recommendation_with_no_history_invents_nothing(db, alice):
    rec = ins.recommend_budgets(db, alice.id, "2026-11", today=TODAY)
    assert rec.items == [] and rec.suggested_total == 0 and any("not enough" in n.lower() for n in rec.notes)


def test_recommendation_reflects_existing_budget(db, alice):
    history(db, alice, {"Food": (1000, 1200, 1500)})
    bs.create_budget(db, alice.id, "2026-11", 1800, cat_id(db, alice, "Food"))
    food = next(i for i in ins.recommend_budgets(db, alice.id, "2026-11", today=TODAY).items if i.category_name == "Food")
    assert food.current_budget == 180_000


def constant_history(db, user):
    history(db, user, {"Rent": (5000,) * 3, "Shopping": (3000,) * 3, "Entertainment": (2000,) * 3})


def test_overall_budget_trims_only_non_essentials_and_explains(db, alice):
    constant_history(db, alice)
    rec = ins.recommend_budgets(db, alice.id, "2026-11", overall_limit=9000, today=TODAY)
    got = {i.category_name: i for i in rec.items}
    assert got["Rent"].suggested == 525_000 and got["Rent"].protected          # essential: untouched
    assert got["Shopping"].suggested == 225_000 and got["Entertainment"].suggested == 150_000
    assert "Reduced from" in got["Shopping"].adjustment
    assert rec.suggested_total <= 900_000 and rec.unresolved_gap == 0
    assert any("fits" in n for n in rec.notes)


def test_overall_budget_too_small_reports_the_gap(db, alice):
    constant_history(db, alice)
    rec = ins.recommend_budgets(db, alice.id, "2026-11", overall_limit=5000, today=TODAY)
    got = {i.category_name: i.suggested for i in rec.items}
    assert got["Rent"] == 525_000 and got["Shopping"] == 189_000 and got["Entertainment"] == 126_000
    assert rec.unresolved_gap == 340_000 and rec.suggested_total == 840_000
    assert any("above your overall budget" in n for n in rec.notes)


def test_generous_overall_budget_reports_unallocated(db, alice):
    constant_history(db, alice)
    rec = ins.recommend_budgets(db, alice.id, "2026-11", overall_limit=20000, today=TODAY)
    assert rec.unallocated == 2_000_000 - 1_050_000 and rec.unresolved_gap == 0


def test_recommendation_applied_only_through_explicit_upsert(db, alice):
    constant_history(db, alice)
    rec = ins.recommend_budgets(db, alice.id, "2026-11", today=TODAY)
    item = rec.items[0]
    bs.upsert_budget(db, alice.id, "2026-11", item.suggested / 100, item.category_id)
    assert len(bs.list_budgets(db, alice.id, "2026-11")) == 1
