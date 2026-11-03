"""Budgets: monthly overall and category budgets, history and suggestions for next month."""
from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from components import charts
from components.forms import month_selector
from components.layout import (
    LEVEL_BADGE, budget_bar, empty_state, flash, friendly_error, metric_card, setup_page, show_chart, show_table,
)
from database import session_scope
from services import adaptive_planning_service as aps
from services import budget_service as bs
from services import insights_service as ins
from services import transaction_service as ts
from utils.dates import month_key, month_label, shift_month
from utils.formatting import escape_md, format_money, format_pct
from utils.money import paise_to_float

profile = setup_page("Budgets", "Set monthly limits, watch how much you have used, and get suggestions for next month.")
today = date.today()
cur = profile.currency
money = lambda p, **kw: format_money(p, cur, **kw)  # noqa: E731
this_month = month_key(today)


def run_action(action, success_message: str) -> bool:
    try:
        with session_scope() as db:
            action(db)
    except Exception as exc:  # noqa: BLE001
        friendly_error(exc)
        return False
    flash("success", success_message)
    st.rerun()
    return True


month = month_selector("bud_month", "Month", today=today)
tab_over, tab_set, tab_reco = st.tabs(["Overview", "Manual + auto budgets", "Suggestions for next month"])

try:
    with session_scope() as db:
        statuses = bs.month_statuses(db, profile.id, month)
        expense_cats = [(c.id, c.name) for c in ts.list_categories(db, profile.id, "expense")]
        history_months = [shift_month(month, -n) for n in range(5, -1, -1)]
        history = bs.overall_history(db, profile.id, history_months)
        prev_month_budgets = bs.list_budgets(db, profile.id, shift_month(month, -1))
        this_budgets = {(b.category_id): b for b, _ in bs.list_budgets(db, profile.id, month)}
        category_budget_sum = bs.category_budget_total(db, profile.id, month)
        auto_budget = aps.auto_budget_recommendations(db, profile.id, month, required_months=5)
except Exception as exc:  # noqa: BLE001
    friendly_error(exc)
    st.stop()

# ====================================================================== overview
with tab_over:
    if not statuses:
        empty_state(f"No budgets for {month_label(month)}",
                    "Use the Set a budget tab to create an overall monthly limit and limits for individual categories.", "🎯")
    else:
        overall = next((s for s in statuses if s.is_overall), None)
        m1, m2, m3 = st.columns(3)
        with m1:
            metric_card("Overall budget", money(overall.limit) if overall else "Not set", month_label(month), "accent")
        with m2:
            metric_card("Spent" if overall else "Spent (all categories)", money(overall.used) if overall else "-",
                        format_pct(overall.utilization, 1) + " of the budget" if overall else "Set an overall budget to compare",
                        {"ok": "positive", "warning": "warning", "exceeded": "negative"}[overall.level] if overall else "neutral")
        with m3:
            if overall:
                label = "Exceeded by" if overall.exceeded_by else "Remaining"
                metric_card(label, money(overall.exceeded_by if overall.exceeded_by else overall.remaining), LEVEL_BADGE[overall.level],
                            {"ok": "positive", "warning": "warning", "exceeded": "negative"}[overall.level])
            else:
                metric_card("Remaining", "-", "No overall budget", "neutral")
        if overall and category_budget_sum > overall.limit:
            st.warning(f"Your category budgets add up to {money(category_budget_sum)}, which is more than your overall budget of "
                       f"{money(overall.limit)} by {money(category_budget_sum - overall.limit)}.")
        st.write("")
        for status in statuses:
            budget_bar(status, profile)
            with st.expander(f"Edit or delete: {status.label}"):
                with st.form(f"edit_budget_{status.budget_id}"):
                    e1, e2 = st.columns([1, 2])
                    new_limit = e1.number_input("Budget limit", min_value=0.01, value=paise_to_float(status.limit), step=100.0,
                                                format="%.2f", key=f"limit_{status.budget_id}")
                    new_desc = e2.text_input("Description (optional)", value=status.description, max_chars=255, key=f"desc_{status.budget_id}")
                    save = st.form_submit_button("Save changes", type="primary")
                if save:
                    run_action(lambda db: bs.update_budget(db, profile.id, status.budget_id, new_limit, new_desc), "Budget updated.")
                confirm_key = f"del_budget_{status.budget_id}"
                if st.session_state.get(confirm_key):
                    st.warning("Delete this budget? Your transactions are not affected.")
                    d1, d2, _ = st.columns([2, 1, 4])
                    if d1.button("Yes, delete budget", type="primary", key=f"yes_{confirm_key}"):
                        st.session_state.pop(confirm_key, None)
                        run_action(lambda db: bs.delete_budget(db, profile.id, status.budget_id), "Budget deleted.")
                    if d2.button("Cancel", key=f"no_{confirm_key}"):
                        st.session_state.pop(confirm_key, None)
                        st.rerun()
                elif st.button("Delete budget", key=confirm_key + "_ask"):
                    st.session_state[confirm_key] = True
                    st.rerun()
            st.write("")
        fig = charts.budget_vs_actual(statuses, cur, f"Budget vs actual spending - {month_label(month)}")
        if fig:
            show_chart(fig, "bud_vs_actual")

    st.subheader("Previous months (overall budget)")
    fig = charts.budget_history(history, cur, "Overall budget vs spending, last 6 months")
    if fig:
        show_chart(fig, "bud_history")
        show_table(pd.DataFrame([{"Month": month_label(m), "Budget": money(s.limit), "Spent": money(s.used),
                                  "Remaining / exceeded": money(s.remaining, signed=True), "Used": format_pct(s.utilization, 1),
                                  "Status": LEVEL_BADGE[s.level]} for m, s in history if s]), hide_index=True)
    else:
        empty_state("No overall budgets in the last six months", "Once you set overall budgets, past months are compared here.", "🗓️")

# ====================================================================== manual and automatic budgets
with tab_set:
    manual_col, auto_col = st.columns([1, 1.08], gap="large")
    with manual_col:
        st.markdown(f"#### Your budget · {month_label(month)}")
        st.caption("Set the limits yourself. These values are always under your control.")
        with st.form("new_budget", clear_on_submit=True):
            scope = st.selectbox("Applies to", [None, *[i for i, _ in expense_cats]], key="nb_scope",
                                 format_func=lambda i: "Overall monthly budget (all expenses)" if i is None else dict(expense_cats)[i])
            n1, n2 = st.columns([1, 2])
            limit = n1.number_input("Budget limit", min_value=0.01, value=None, step=100.0, format="%.2f", placeholder="0.00", key="nb_limit")
            desc = n2.text_input("Description (optional)", max_chars=255, key="nb_desc")
            create = st.form_submit_button("Create manual budget", type="primary")
        if create:
            if scope in this_budgets or (scope is None and None in this_budgets):
                st.error("A budget for this month and scope already exists. Edit it from the Overview tab.")
            else:
                run_action(lambda db: bs.create_budget(db, profile.id, month, limit, scope, desc), "Budget created.")
        st.caption("Manual budgets are based on the amount you choose. Income is never counted as an expense.")

        st.divider()
        st.markdown("#### Copy last month's manual budgets")
        missing = [(b, label) for b, label in prev_month_budgets if b.category_id not in this_budgets]
        if not prev_month_budgets:
            st.caption(f"{month_label(shift_month(month, -1))} has no budgets to copy.")
        elif not missing:
            st.caption("Every budget from last month already exists for this month.")
        else:
            st.write(f"{len(missing)} budget(s) from {month_label(shift_month(month, -1))} are not set for {month_label(month)}: "
                     + ", ".join(escape_md(label) for _, label in missing) + ". Existing budgets are never overwritten.")
            if st.button(f"Copy {len(missing)} budget(s)", key="copy_budgets"):
                def copy(db):
                    for budget_row, _ in missing:
                        bs.create_budget(db, profile.id, month, paise_to_float(budget_row.limit_amount), budget_row.category_id, budget_row.description)
                run_action(copy, "Budgets copied.")

    with auto_col:
        st.markdown("#### ✨ Automatic budget")
        st.caption("Uses your actual expense transactions—not manually typed guesses.")
        if not auto_budget.ready:
            progress = len(auto_budget.basis_months)
            st.info(f"Unlocks after expenses have been recorded in five different completed months. Progress: {progress}/5 months.")
            if auto_budget.basis_months:
                st.caption("Months found: " + ", ".join(month_label(m, short=True) for m in auto_budget.basis_months))
            else:
                st.caption("Start recording expenses. The automatic suggestions will become available when enough history exists.")
        elif not auto_budget.items:
            empty_state("No category history found", "Record expenses in the five completed months before this budget month.", "📊")
        else:
            st.success("Unlocked · based on five completed months of recorded expenses")
            st.caption("Basis: " + ", ".join(month_label(m, short=True) for m in auto_budget.basis_months))
            st.metric("Suggested total across categories", money(auto_budget.suggested_total))
            editor_rows = []
            editor_index = []
            for item in auto_budget.items:
                existing = this_budgets.get(item.category_id)
                editor_rows.append({
                    "Apply": existing is None,
                    "Category": item.name,
                    "Current manual budget": paise_to_float(existing.limit_amount) if existing else None,
                    "Auto suggestion": paise_to_float(item.average_monthly),
                })
                editor_index.append(item.category_id)
            auto_editor = st.data_editor(
                pd.DataFrame(editor_rows, index=editor_index),
                hide_index=True,
                key=f"auto_budget_editor_{month}",
                disabled=["Category", "Current manual budget"],
                column_config={
                    "Apply": st.column_config.CheckboxColumn("Apply", help="Select the category budget(s) to create or update."),
                    "Current manual budget": st.column_config.NumberColumn("Current budget", format="%.2f"),
                    "Auto suggestion": st.column_config.NumberColumn("Suggested limit", min_value=0.0, step=50.0, format="%.2f"),
                },
            )
            chosen = auto_editor[auto_editor["Apply"] & (auto_editor["Auto suggestion"] > 0)]
            overwrite_count = sum(1 for category_id in chosen.index if category_id in this_budgets)
            st.caption("Suggestions use the average monthly spend in those five months. You can edit every suggested amount before applying.")
            confirm = st.checkbox(
                f"I reviewed these amounts and approve {len(chosen)} selected budget(s)" +
                (f", including {overwrite_count} existing budget(s) that will be updated." if overwrite_count else "."),
                key=f"auto_budget_confirm_{month}",
            )
            if st.button("Apply automatic budgets", type="primary", disabled=chosen.empty or not confirm,
                         key=f"apply_auto_budgets_{month}"):
                def apply_auto(db):
                    for category_id, row in chosen.iterrows():
                        bs.upsert_budget(db, profile.id, month, float(row["Auto suggestion"]), int(category_id),
                                         "Automatic suggestion from five months of recorded expenses")
                run_action(apply_auto, f"Applied {len(chosen)} automatic category budget(s) for {month_label(month)}.")

# ====================================================================== suggestions
with tab_reco:
    st.write("Suggestions are calculated from your real spending history. Nothing is saved until you review the amounts and apply them.")
    r1, r2, r3 = st.columns(3)
    with r1:
        target = month_selector("reco_month", "Suggest budgets for", today=today, default=shift_month(this_month, 1))
    lookback = r2.slider("Months of history to use", 1, 6, 3, key="reco_lookback")
    overall_in = r3.number_input("Your overall budget for that month (optional)", min_value=0.0, value=None, step=500.0,
                                 format="%.2f", placeholder="Leave empty for none", key="reco_overall",
                                 help="If you enter one, non-essential categories are trimmed to fit it and any gap is explained.")
    b1, b2, _ = st.columns([1, 1, 4])
    if b1.button("Generate suggestions", type="primary", key="reco_generate"):
        try:
            with session_scope() as db:
                st.session_state["bud_reco"] = ins.recommend_budgets(db, profile.id, target, lookback_months=lookback,
                                                                     overall_limit=overall_in or None, today=today)
        except Exception as exc:  # noqa: BLE001
            friendly_error(exc)
    if b2.button("Discard suggestions", key="reco_discard"):
        st.session_state.pop("bud_reco", None)

    rec = st.session_state.get("bud_reco")
    if rec is not None:
        st.divider()
        st.markdown(f"#### Suggestions for {month_label(rec.target_month)}")
        if not rec.items:
            empty_state("Not enough history yet", " ".join(rec.notes) or "Record some expenses first.", "🧭")
        else:
            conf = {"high": "High confidence", "medium": "Medium confidence", "low": "Low confidence (starting estimate)"}[rec.confidence]
            st.markdown(f"**{conf}.** Based on {escape_md(rec.basis_label)}.")
            for n in rec.notes:
                (st.warning if "above your overall" in n or "Starting estimate" in n or "higher than your average" in n else st.info)(n)
            with st.expander("Assumptions used"):
                for a in rec.assumptions:
                    st.markdown(f"- {escape_md(a)}")
            overall_default = rec.overall_limit or rec.suggested_total
            rows = [{"Apply": False, "Budget for": "Overall budget", "Your current budget": None, "Suggested limit": paise_to_float(overall_default),
                     "Confidence": "From your input" if rec.overall_limit else "Sum of categories"}]
            index = [0]
            for it in rec.items:
                rows.append({"Apply": True, "Budget for": it.category_name,
                             "Your current budget": paise_to_float(it.current_budget) if it.current_budget else None,
                             "Suggested limit": paise_to_float(it.suggested), "Confidence": it.confidence.capitalize()})
                index.append(it.category_id)
            editor = st.data_editor(
                pd.DataFrame(rows, index=index), hide_index=True, key=f"reco_editor_{rec.target_month}",
                disabled=["Budget for", "Your current budget", "Confidence"],
                column_config={"Suggested limit": st.column_config.NumberColumn(min_value=0.0, step=10.0, format="%.2f",
                                                                                help="You can edit this amount before applying."),
                               "Your current budget": st.column_config.NumberColumn(format="%.2f")})
            st.caption(f"Category suggestions add up to {money(rec.suggested_total)}."
                       + (f" Unassigned part of your overall budget: {money(rec.unallocated)}." if rec.unallocated else "")
                       + (f" Still above your overall budget by {money(rec.unresolved_gap)}." if rec.unresolved_gap else ""))
            with st.expander("Why these amounts?"):
                for it in rec.items:
                    st.markdown(f"**{escape_md(it.category_name)}** - suggested {money(it.suggested)} ({it.confidence} confidence)")
                    st.caption(escape_md(it.explanation + (" " + it.adjustment if it.adjustment else "")))
            chosen = editor[editor["Apply"] & (editor["Suggested limit"] > 0)]
            ok = st.checkbox(f"Yes, create or update {len(chosen)} budget(s) for {month_label(rec.target_month)} with the amounts shown. "
                             "Existing budgets for the same scope will be changed.", key="reco_confirm")
            if st.button("Apply selected budgets", type="primary", disabled=not ok or chosen.empty, key="reco_apply"):
                def apply(db):
                    for idx, row in chosen.iterrows():
                        bs.upsert_budget(db, profile.id, rec.target_month, float(row["Suggested limit"]), None if idx == 0 else int(idx),
                                         "Applied from suggestion")
                    st.session_state.pop("bud_reco", None)   # only reached if every budget saved without error
                run_action(apply, f"Applied {len(chosen)} budget(s) for {month_label(rec.target_month)}.")
