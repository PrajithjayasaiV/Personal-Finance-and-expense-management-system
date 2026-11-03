"""Reports: filtered CSV export of transactions, period summaries and other exports."""
from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

import config
from components.forms import date_range_selector, id_selectbox, month_selector
from components.layout import empty_state, friendly_error, metric_card, setup_page, show_table
from database import session_scope
from services import report_service as rs
from services import transaction_service as ts
from utils.dates import month_key, shift_month
from utils.formatting import format_date, format_money, format_pct

profile = setup_page("Reports", "Export your records and review a summary for any period.")
today = date.today()
cur = profile.currency
money = lambda p, **kw: format_money(p, cur, **kw)  # noqa: E731

tab_tx, tab_sum, tab_other = st.tabs(["Export transactions", "Summary report", "Budgets and goals"])

# ====================================================================== transactions export
with tab_tx:
    try:
        with session_scope() as db:
            categories = [(c.id, f"{c.name} ({c.type})") for c in ts.list_categories(db, profile.id)]
    except Exception as exc:  # noqa: BLE001
        friendly_error(exc)
        st.stop()
    st.markdown("#### Filters")
    rng = date_range_selector("rep", profile, presets=("all_time", "current_month", "previous_month", "last_30_days", "last_90_days",
                                                      "current_year", "custom"), default="current_month", today=today)
    f1, f2, f3 = st.columns(3)
    kind = f1.selectbox("Transaction type", ["All", "Income", "Expense"], key="rep_type")
    cat = id_selectbox("Category", [(i, n) for i, n in categories if kind == "All" or n.endswith(f"({kind.lower()})")], "rep_cat", allow_all="All categories")
    method = f3.selectbox("Payment method", ["All", *config.PAYMENT_METHODS], key="rep_method")
    filters = ts.TransactionFilters(start=rng.start, end=rng.end, type=None if kind == "All" else kind.lower(), category_id=cat,
                                    payment_method=None if method == "All" else method)
    try:
        with session_scope() as db:
            blob, count = rs.export_transactions_csv(db, profile.id, filters)
            preview, _ = ts.query_transactions(db, profile.id, filters, limit=15)
    except Exception as exc:  # noqa: BLE001
        friendly_error(exc)
        st.stop()
    if count == 0:
        empty_state("No transactions match these filters", "Nothing to export. Widen the date range or clear a filter.", "🧾")
    else:
        st.write(f"**{count}** transaction(s) will be exported. Preview of the newest {len(preview)}:")
        show_table(pd.DataFrame([{"Date": format_date(r.txn_date, profile.date_format), "Type": r.type.capitalize(), "Category": r.category_name,
                                  "Amount": money(r.amount), "Payment method": r.payment_method, "Description": r.description} for r in preview]),
                   hide_index=True)
    st.download_button("Download transactions as CSV", data=blob, file_name=f"transactions_{today.isoformat()}.csv", mime="text/csv",
                       disabled=count == 0, type="primary", key="dl_transactions")
    st.caption("The file contains only your own records. Text that could be read as a spreadsheet formula is prefixed with an apostrophe for safety.")

# ====================================================================== summary
with tab_sum:
    srng = date_range_selector("sum", profile, presets=("this_week", "last_week", "current_month", "previous_month", "last_30_days", "custom"),
                               default="current_month", today=today)
    try:
        with session_scope() as db:
            summary = rs.build_summary(db, profile.id, srng.start, srng.end)
    except Exception as exc:  # noqa: BLE001
        friendly_error(exc)
        st.stop()
    st.markdown(f"#### Summary for {srng.label}")
    if summary.transaction_count == 0:
        empty_state("No transactions in this period", "The summary will list totals, categories and budgets once you have records here.", "🗒️")
    s1, s2, s3, s4 = st.columns(4)
    with s1:
        metric_card("Income", money(summary.income), f"{summary.transaction_count} transaction(s) in total", "positive")
    with s2:
        metric_card("Expenses", money(summary.expense), "", "neutral")
    with s3:
        metric_card("Net cash flow", money(summary.net, signed=True), "Income minus expenses", "positive" if summary.net >= 0 else "negative")
    with s4:
        metric_card("Savings rate", format_pct(summary.savings_rate, 1) if summary.savings_rate is not None else "Not available",
                    "" if summary.savings_rate is not None else "No income in this period", "neutral")
    if summary.expense_categories:
        st.markdown("**Expenses by category**")
        show_table(pd.DataFrame([{"Category": c.name, "Amount": money(c.total), "Share": format_pct(c.share_pct, 1)} for c in summary.expense_categories]), hide_index=True)
    if summary.income_categories:
        st.markdown("**Income by category**")
        show_table(pd.DataFrame([{"Category": c.name, "Amount": money(c.total), "Share": format_pct(c.share_pct, 1)} for c in summary.income_categories]), hide_index=True)
    if summary.budgets:
        st.markdown("**Budget status** (full-month figures for each month the period touches)")
        show_table(pd.DataFrame([{"Month": b.month, "Budget": b.label, "Limit": money(b.limit), "Spent": money(b.used),
                                  "Remaining / exceeded": money(b.remaining, signed=True), "Used": format_pct(b.utilization, 1)} for b in summary.budgets]),
                   hide_index=True)
    st.download_button("Download summary as CSV", data=rs.summary_csv(summary), file_name=f"summary_{srng.start}_{srng.end}.csv",
                       mime="text/csv", type="primary", key="dl_summary")

# ====================================================================== budgets and goals
with tab_other:
    st.markdown("#### Budgets")
    b1, b2 = st.columns(2)
    with b1:
        first_m = month_selector("rep_bud_from", "From month", today=today, back=36, forward=0, default=shift_month(month_key(today), -5))
    with b2:
        last_m = month_selector("rep_bud_to", "To month", today=today, back=36, forward=12, default=month_key(today))
    if last_m < first_m:
        st.error("The last month must not be earlier than the first month.")
    else:
        try:
            with session_scope() as db:
                bblob, bcount = rs.export_budgets_csv(db, profile.id, first_m, last_m)
                cblob, ccount = rs.export_contributions_csv(db, profile.id)
        except Exception as exc:  # noqa: BLE001
            friendly_error(exc)
            st.stop()
        st.download_button("Download budgets as CSV", data=bblob, file_name=f"budgets_{first_m}_to_{last_m}.csv", mime="text/csv",
                           disabled=bcount == 0, key="dl_budgets")
        if bcount == 0:
            st.caption("No budgets exist in this month range.")
        st.markdown("#### Savings goal contributions")
        st.download_button("Download contributions as CSV", data=cblob, file_name="goal_contributions.csv", mime="text/csv",
                           disabled=ccount == 0, key="dl_contributions")
        if ccount == 0:
            st.caption("You have not recorded any goal contributions yet.")
