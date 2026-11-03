"""A focused dashboard: monthly totals, spending room, trends and next actions."""
from __future__ import annotations

import hashlib
from datetime import date

import pandas as pd
import streamlit as st

from components import charts
from components.forms import date_range_selector
from components.layout import (
    SEVERITY_ICON, budget_bar, empty_state, friendly_error, metric_card, page_link, progress_bar,
    setup_page, show_chart, show_table,
)
from database import session_scope
from services import adaptive_planning_service as aps
from services import analytics_service as an
from services import budget_service as bs
from services import insights_service as ins
from services import notification_service as ns
from services import savings_service as sv
from services import transaction_service as ts
from utils.dates import month_key, month_label, shift_month
from utils.formatting import escape_md, format_date, format_money, format_pct

profile = setup_page("Your money, at a glance", "A clear view of what came in, what went out, and what needs attention.")
today = date.today()
cur = profile.currency
money = lambda p, **kw: format_money(p, cur, **kw)  # noqa: E731
rng = date_range_selector("dash", profile, today=today)
this_month = month_key(today)

try:
    with session_scope() as db:
        ns.refresh_all(db, profile.id, today)
        txn_count = ts.count_transactions(db, profile.id)
        totals = an.period_totals(db, profile.id, rng.start, rng.end)
        statuses = bs.month_statuses(db, profile.id, this_month)
        overall = next((s for s in statuses if s.is_overall), None)
        category_items = an.category_totals(db, profile.id, rng.start, rng.end)
        safe = ins.safe_to_spend(db, profile.id, today)
        goals = sv.list_progress(db, profile.id, today, include_completed=False)
        insight_list = ins.generate_insights(db, profile.id, rng.start, rng.end, today=today, limit=3)
        recent = ts.recent_transactions(db, profile.id, 5)
        trend_months = st.session_state.get("dash_trend_months", 6)
        trend = an.monthly_series(db, profile.id, shift_month(this_month, -(trend_months - 1)), this_month)
        salary_history = aps.five_month_history(db, profile.id, this_month)
        current_month_income = an.period_totals(db, profile.id, today.replace(day=1), today).income
        detected_income_drop = aps.possible_income_drop(salary_history, current_month_income)
except Exception as exc:  # noqa: BLE001
    friendly_error(exc)
    st.stop()

if txn_count == 0:
    empty_state("Your dashboard is ready", "Add your first income or expense. Your personal spending insights will appear as you build a transaction history.", "✨")
    page_link("views/2_Transactions.py", "Add your first transaction", "➕")

st.caption(f"Showing **{rng.label}**")
c1, c2, c3 = st.columns(3)
with c1:
    metric_card("Income received", money(totals.income), "For the selected period", "positive")
with c2:
    metric_card("Expenses recorded", money(totals.expense), "For the selected period", "negative" if totals.expense > totals.income else "neutral")
with c3:
    if overall is None:
        metric_card("Monthly budget", "Not set", "Create one in Budgets", "neutral")
    else:
        metric_card("Budget used", format_pct(overall.utilization, 1), f"{money(overall.used)} spent of {money(overall.limit)}", 
                    {"ok": "positive", "warning": "warning", "exceeded": "negative"}[overall.level])

if detected_income_drop is not None:
    st.warning(
        f"Possible income decrease detected: this month's recorded income is {format_pct(detected_income_drop, 1)} "
        f"below your average across the previous five months. Check that the full month's salary has been recorded, "
        "then review a revised spending plan."
    )
    page_link("views/9_Income_Change_Plan.py", "Review the income change and create a new spending plan", "🧭")

# The single most useful estimate, without repeating balance and budget figures in several cards.
st.subheader("Your monthly spending room")
s1, s2, s3 = st.columns([1.35, 1, 1])
with s1:
    metric_card("Estimated safe to spend", money(safe.estimate),
                "After reserve and upcoming commitments" if safe.commitments else "After your reserve", "accent")
with s2:
    metric_card("Reserve to protect", money(safe.reserve), "Your setting", "neutral")
with s3:
    metric_card("Upcoming commitments", money(safe.commitments), "Bills and payments you entered", "neutral")
with st.expander("How is safe-to-spend calculated?"):
    for line in safe.explanation:
        st.markdown(f"- {escape_md(line)}")
    for warning in safe.warnings:
        st.caption(f"⚠ {warning}")

# Two visuals are enough for the home screen. Detailed comparisons stay in Analytics.
left, right = st.columns(2)
with left:
    st.markdown("**Where your money went**")
    fig = charts.category_doughnut(category_items, cur, "Expenses by category")
    if fig:
        show_chart(fig, "dash_category_pie")
    else:
        empty_state("No expenses in this period", "Record an expense or change the selected date range.", "🥧")
with right:
    st.markdown("**Monthly spending trend**")
    trend_months = st.selectbox("Months", [3, 6, 12], index=[3, 6, 12].index(trend_months), key="dash_trend_months")
    if trend_months != len(trend):
        try:
            with session_scope() as db:
                trend = an.monthly_series(db, profile.id, shift_month(this_month, -(trend_months - 1)), this_month)
        except Exception as exc:  # noqa: BLE001
            friendly_error(exc)
            trend = []
    fig = charts.expense_trend(trend, cur, f"Expenses over the last {trend_months} months")
    if fig:
        show_chart(fig, "dash_trend")
    else:
        empty_state("No spending history yet", "Your spending trend will appear after you record expenses.", "📉")

b_col, g_col = st.columns(2)
with b_col:
    st.subheader(f"Budget status · {month_label(this_month)}")
    if not statuses:
        empty_state("No budget set", "Create a manual budget or unlock automatic budgets after five expense months.", "🎯")
    else:
        for status in statuses[:4]:
            budget_bar(status, profile)
            st.write("")
    page_link("views/3_Budgets.py", "Manage budgets", "🎯")
with g_col:
    st.subheader("Savings goals")
    if not goals:
        empty_state("No active savings goals", "Add a goal to track progress towards something important.", "🐖")
    for goal in goals[:3]:
        st.markdown(f"**{escape_md(goal.name)}** · {format_pct(goal.pct, 1)} saved")
        progress_bar(goal.display_pct, "accent")
        st.caption(f"{money(goal.saved)} of {money(goal.target)} · {money(goal.remaining)} remaining"
                   + (f" · due {format_date(goal.target_date, profile.date_format)}" if goal.target_date else ""))
    page_link("views/4_Savings_Goals.py", "Manage savings goals", "🐖")

if insight_list:
    st.subheader("A few things to notice")
    for item in insight_list:
        with st.container(border=True):
            st.markdown(f"{SEVERITY_ICON[item.severity]} **{escape_md(item.title)}**")
            st.markdown(escape_md(item.message))
            if item.evidence:
                st.caption(escape_md(item.evidence))
            digest = hashlib.md5(item.key.encode()).hexdigest()[:10]
            a, b, _ = st.columns([1, 1, 6])
            action = "acknowledged" if a.button("Got it", key=f"ack_{digest}") else (
                "dismissed" if b.button("Dismiss", key=f"dis_{digest}") else None)
            if action:
                try:
                    with session_scope() as db:
                        ins.set_feedback(db, profile.id, item.key, action)
                except Exception as exc:  # noqa: BLE001
                    friendly_error(exc)
                else:
                    st.rerun()

st.subheader("Recent activity")
if not recent:
    empty_state("No transactions yet", "Your recent income and expenses will appear here.", "💳")
else:
    df = pd.DataFrame([{
        "Date": format_date(row.txn_date, profile.date_format),
        "Type": row.type.capitalize(),
        "Category": row.category_name,
        "Description": row.description,
        "Amount": money(row.amount if row.type == "income" else -row.amount, signed=True),
    } for row in recent])
    show_table(df, hide_index=True)
page_link("views/2_Transactions.py", "See all transactions", "💳")
