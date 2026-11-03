"""Analytics: category analysis, trends, period comparison, budget performance and payment methods."""
from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from components import charts
from components.forms import date_range_selector, month_selector
from components.layout import empty_state, friendly_error, metric_card, setup_page, show_chart, show_table
from database import session_scope
from services import analytics_service as an
from services import budget_service as bs
from services import savings_service as sv
from utils.dates import month_key, month_label, previous_comparable_period, shift_month
from utils.formatting import format_money, format_pct, format_range

profile = setup_page("Analytics", "Understand your spending patterns over any period.")
today = date.today()
cur = profile.currency
money = lambda p, **kw: format_money(p, cur, **kw)  # noqa: E731

rng = date_range_selector("ana", profile, today=today)
st.caption(f"Selected period: **{rng.label}**")
tab_spend, tab_trend, tab_cmp, tab_budget, tab_pay = st.tabs(
    ["Spending", "Trends", "Period comparison", "Budget performance", "Payment methods"])

# ====================================================================== spending
with tab_spend:
    try:
        with session_scope() as db:
            cats = an.category_totals(db, profile.id, rng.start, rng.end)
            avg_rows = an.average_monthly_category_spending(db, profile.id, rng.start, rng.end)
            totals = an.period_totals(db, profile.id, rng.start, rng.end)
    except Exception as exc:  # noqa: BLE001
        friendly_error(exc)
        st.stop()
    if not cats:
        empty_state("No expenses in this period", "Record expenses or pick a wider date range to analyse your spending.", "🥧")
    else:
        a, b = st.columns(2)
        with a:
            show_chart(charts.category_doughnut(cats, cur, "Share of expenses by category"), "ana_pie")
        with b:
            show_chart(charts.category_bars(cats, cur, "Highest-spending categories"), "ana_bars")
        avg = {name: avg_ for name, avg_, _ in avg_rows}
        show_table(pd.DataFrame([{"Category": c.name, "Total spent": money(c.total), "Share of expenses": format_pct(c.share_pct, 1),
                                  "Average per month": money(avg.get(c.name, 0))} for c in cats]), hide_index=True)
        st.caption(f"Total expenses {money(totals.expense)}. 'Average per month' divides each category total by the number of calendar months "
                   "the selected period touches.")

# ====================================================================== trends
with tab_trend:
    months = st.selectbox("Months to show", [3, 6, 12, 24], index=1, key="ana_months")
    last = month_key(today)
    try:
        with session_scope() as db:
            points = an.monthly_series(db, profile.id, shift_month(last, -(months - 1)), last)
            contributions = sv.contributions_by_month(db, profile.id)
    except Exception as exc:  # noqa: BLE001
        friendly_error(exc)
        st.stop()
    fig = charts.income_expense_bars(points, "month", cur, f"Income vs expenses, last {months} months")
    if fig is None:
        empty_state("No activity in these months", "Trends appear once you have recorded income or expenses.", "📈")
    else:
        show_chart(fig, "ana_inc_exp")
        t1, t2 = st.columns(2)
        for col, fig, key, empty_msg in (
                (t1, charts.expense_trend(points, cur, "Monthly expenses"), "ana_exp_trend", "No expenses recorded in these months."),
                (t2, charts.savings_trend(points, contributions, cur, "Savings trend"), "ana_savings", "No savings data for these months.")):
            with col:
                if fig:
                    show_chart(fig, key)
                else:
                    st.info(empty_msg)
        show_table(pd.DataFrame([{"Month": month_label(p.period_start.strftime("%Y-%m")), "Income": money(p.income), "Expenses": money(p.expense),
                                  "Net cash flow": money(p.net, signed=True),
                                  "Savings rate": format_pct(an.savings_rate(p.income, p.expense), 1) if p.income else "n/a (no income)"}
                                 for p in points]), hide_index=True)
        st.caption("Savings trend shows the recorded surplus (income minus expenses). It is a calculation, not money moved to a savings account. "
                   "Goal contributions are the amounts you logged on the Savings goals page.")

# ====================================================================== period comparison
with tab_cmp:
    if rng.start is None:
        st.info("Choose a specific date range to compare it with the previous period.")
    else:
        prev_start, prev_end = previous_comparable_period(rng.start, rng.end)
        try:
            with session_scope() as db:
                now_t = an.period_totals(db, profile.id, rng.start, rng.end)
                prev_t = an.period_totals(db, profile.id, prev_start, prev_end)
                cat_cmp = an.compare_categories(db, profile.id, rng.start, rng.end, prev_start, prev_end)
                first = an.first_transaction_date(db, profile.id)
        except Exception as exc:  # noqa: BLE001
            friendly_error(exc)
            st.stop()
        st.write(f"Comparing **{rng.label}** with the previous comparable period, "
                 f"**{format_range(prev_start, prev_end, profile.date_format)}**.")
        if first is None or first > prev_start:
            st.info("Your records start after the beginning of the previous period, so the comparison may understate it.")
        c1, c2, c3 = st.columns(3)
        for col, label, cur_v, prev_v, good_when_up in ((c1, "Income", now_t.income, prev_t.income, True),
                                                          (c2, "Expenses", now_t.expense, prev_t.expense, False),
                                                          (c3, "Net cash flow", now_t.net, prev_t.net, True)):
            cmp = an.compare(cur_v, prev_v)
            with col:
                if cmp.available and cmp.change_pct is not None:
                    sub = f"{money(cmp.change, signed=True)} ({format_pct(cmp.change_pct, 1, signed=True)}) vs {money(prev_v)}"
                else:
                    sub = f"Previous: {money(prev_v)}. No percentage (previous value is zero or negative)."
                improving = (cmp.change >= 0) == good_when_up
                metric_card(label, money(cur_v, signed=(label == "Net cash flow")), sub, "neutral" if cmp.change == 0 else ("positive" if improving else "warning"))
        fig = charts.comparison_bars(cat_cmp, cur, "Expenses by category: previous vs selected period")
        if fig is None:
            empty_state("No expenses to compare", "Neither period has recorded expenses.", "⚖️")
        else:
            show_chart(fig, "ana_cmp")
            show_table(pd.DataFrame([{"Category": name, "Selected period": money(c.current), "Previous period": money(c.previous),
                                      "Change": money(c.change, signed=True),
                                      "Change %": format_pct(c.change_pct, 1, signed=True) if c.available else "n/a (no previous spending)"}
                                     for name, c in cat_cmp]), hide_index=True)

# ====================================================================== budget performance
with tab_budget:
    month = month_selector("ana_bud_month", "Month", today=today, back=24, forward=0)
    try:
        with session_scope() as db:
            statuses = bs.month_statuses(db, profile.id, month)
            history = bs.overall_history(db, profile.id, [shift_month(month, -n) for n in range(5, -1, -1)])
    except Exception as exc:  # noqa: BLE001
        friendly_error(exc)
        st.stop()
    fig = charts.budget_vs_actual(statuses, cur, f"Budget vs actual - {month_label(month)}")
    if fig is None:
        empty_state(f"No budgets for {month_label(month)}", "Create budgets on the Budgets page to see how you performed.", "🎯")
    else:
        show_chart(fig, "ana_budget_actual")
        show_table(pd.DataFrame([{"Budget": s.label, "Limit": money(s.limit), "Spent": money(s.used),
                                  "Remaining / exceeded": money(s.remaining, signed=True), "Used": format_pct(s.utilization, 1),
                                  "Status": bs.LEVEL_LABELS[s.level]} for s in statuses]), hide_index=True)
    fig = charts.budget_history(history, cur, "Overall budget performance, last 6 months")
    if fig:
        show_chart(fig, "ana_budget_history")

# ====================================================================== payment methods
with tab_pay:
    kind = st.radio("Show", ["Expenses", "Income"], horizontal=True, key="ana_pay_kind")
    try:
        with session_scope() as db:
            rows = an.payment_method_totals(db, profile.id, rng.start, rng.end, kind[:-1].lower() if kind == "Income" else "expense")
    except Exception as exc:  # noqa: BLE001
        friendly_error(exc)
        st.stop()
    fig = charts.payment_method_chart(rows, cur, f"{kind} by payment method")
    if fig is None:
        empty_state(f"No {kind.lower()} in this period", "Nothing to show for the selected dates.", "💳")
    else:
        show_chart(fig, "ana_payment")
        grand = sum(t for _, _, t in rows)
        show_table(pd.DataFrame([{"Payment method": m, "Transactions": n, "Total": money(t), "Share": format_pct(100 * t / grand, 1)} for m, n, t in rows]),
                   hide_index=True)
        st.caption("Payment methods are labels you chose when recording transactions. The app is not connected to any bank, card or UPI service.")
