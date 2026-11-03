"""Compare a new monthly income with five months of history and rebuild category limits."""
from __future__ import annotations

import html
from datetime import date

import pandas as pd
import streamlit as st

from components.forms import month_selector
from components.layout import empty_state, metric_card, setup_page, show_table
from database import session_scope
from services import adaptive_planning_service as aps
from services import budget_service as bs
from services import analytics_service as an
from utils.dates import month_bounds, month_key, month_label
from utils.formatting import format_money, format_pct
from utils.money import paise_to_float, to_paise

profile = setup_page(
    "Income change planner",
    "See how a lower or higher salary changes the monthly spending plan, using your own transaction history.",
)
today = date.today()
cur = profile.currency
money = lambda amount: format_money(amount, cur)  # noqa: E731

effective_month = month_selector("income_plan_month", "Month the new income starts", today=today,
                                 back=36, forward=6, default=month_key(today))
start, end = month_bounds(effective_month)
observed_end = min(end, today) if start <= today else end

try:
    with session_scope() as db:
        history = aps.five_month_history(db, profile.id, effective_month)
        observed_income = an.period_totals(db, profile.id, start, observed_end).income if start <= observed_end else 0
        existing_month_budgets = bs.list_budgets(db, profile.id, effective_month)
except Exception as exc:  # noqa: BLE001
    st.error(f"Could not load your income history: {exc}")
    st.stop()

baseline_default = observed_income if observed_income > 0 else history.average_income
st.markdown("### Enter the new monthly take-home income")
st.caption(f"The comparison uses the five complete months before {month_label(effective_month)}: "
            + ", ".join(month_label(m, short=True) for m in history.months) + ".")
with st.expander("Review the five months of history used"):
    history_rows = [{
        "Month": month_label(month),
        "Recorded income": money(history.income_by_month[month]),
        "Recorded expenses": money(history.expense_by_month[month]),
    } for month in history.months]
    show_table(pd.DataFrame(history_rows), hide_index=True)

if history.income_months_recorded < 5:
    st.info(
        f"You have income recorded in {history.income_months_recorded} of those five months. "
        "You can still preview a plan, but the baseline will be provisional until all five months have income records."
    )

with st.container(border=True):
    i1, i2, i3 = st.columns(3)
    new_income_value = i1.number_input(
        f"New monthly income ({cur})", min_value=0.0, value=paise_to_float(baseline_default),
        step=500.0, format="%.2f", key=f"new_monthly_income_{effective_month}",
        help="Enter the new take-home amount you expect each month, after salary deductions."
    )
    reserve_value = i2.number_input(
        f"Monthly reserve ({cur})", min_value=0.0, value=paise_to_float(profile.min_reserve),
        step=100.0, format="%.2f", key=f"plan_reserve_{effective_month}",
        help="Money you want to protect rather than allocate to everyday spending."
    )
    commitment_value = i3.number_input(
        f"Fixed commitments ({cur})", min_value=0.0, value=paise_to_float(profile.upcoming_commitments),
        step=100.0, format="%.2f", key=f"plan_commitments_{effective_month}",
        help="Monthly bills or payments that need to be paid before flexible spending."
    )

try:
    plan = aps.make_salary_adjustment_plan(
        history,
        to_paise(new_income_value, "New monthly income", allow_zero=True),
        to_paise(reserve_value, "Monthly reserve", allow_zero=True),
        to_paise(commitment_value, "Fixed commitments", allow_zero=True),
    )
except Exception as exc:  # noqa: BLE001
    st.error(f"Could not calculate this plan: {exc}")
    st.stop()

observed_drop = aps.possible_income_drop(history, observed_income)
if effective_month == month_key(today) and observed_drop is not None:
    st.warning(
        f"Possible salary decrease detected. Income recorded so far in {month_label(effective_month)} is "
        f"{format_pct(observed_drop, 1)} below the average income of the previous five months. "
        "Check that the full salary transaction has been entered."
    )

st.divider()
st.markdown("### Your revised monthly picture")
k1, k2, k3, k4 = st.columns(4)
with k1:
    metric_card("Previous monthly income", money(plan.baseline_income), "Average of months with recorded income", "neutral")
with k2:
    metric_card("New monthly income", money(plan.new_income), "Your new take-home estimate", "positive")
with k3:
    if plan.income_change_pct is None:
        metric_card("Income change", "No baseline", "Record income history to compare", "neutral")
    else:
        metric_card("Income change", format_pct(plan.income_change_pct, 1, signed=True),
                    "Decrease" if plan.income_decreased else "Increase / unchanged",
                    "negative" if plan.income_decreased else "positive")
with k4:
    metric_card("Planned expense limits", money(plan.recommended_expenses), "Total of recommended category limits", "accent")

if plan.income_decreased:
    st.warning(
        f"Your new monthly income is {money(plan.baseline_income - plan.new_income)} lower than the historical average. "
        "The revised plan protects the essential categories first, then reduces flexible categories to fit."
    )
elif plan.baseline_income and plan.new_income > plan.baseline_income:
    st.success("Your entered income is higher than the historical average. The plan keeps spending near historical levels rather than automatically increasing lifestyle costs.")

r1, r2, r3 = st.columns(3)
with r1:
    metric_card("Protected essentials (history average)", money(plan.essential_average), "Not automatically cut", "positive")
with r2:
    metric_card("Flexible spending (history average)", money(plan.flexible_average), "Categories that can be adjusted", "neutral")
with r3:
    metric_card("Suggested monthly reduction", money(plan.total_reduction), "Compared with historical category averages", "warning" if plan.total_reduction else "positive")

if plan.essential_shortfall > 0:
    st.error(
        f"The new income cannot cover the historical average for essential categories after reserve and fixed commitments. "
        f"The essential shortfall is {money(plan.essential_shortfall)} per month. Review the reserve, commitments, or essential costs; "
        "do not treat the generated flexible limits as sufficient until this gap is resolved."
    )
else:
    st.info(f"After the reserve and fixed commitments, the available expense capacity is {money(plan.spending_capacity)}. "
            f"The plan leaves {money(plan.remaining_after_plan)} unallocated as a cushion.")

st.markdown("### Category-by-category revised plan")
if not plan.categories:
    empty_state("No expense history found", "Record expenses in the previous months to receive category-specific recommendations.", "📊")
else:
    historical_by_id = {item.category_id: item for item in history.category_averages}
    rows = [{
        "Category": item.name,
        "Priority": "Essential" if item.essential else "Flexible",
        "5-month total": money(historical_by_id[item.category_id].total),
        "Average / month": money(item.historical_average),
        "Suggested new limit": money(item.recommended_limit),
        "Suggested reduction": money(item.suggested_reduction),
    } for item in plan.categories]
    show_table(pd.DataFrame(rows), hide_index=True)
    cuts = sorted((item for item in plan.categories if item.suggested_reduction > 0),
                  key=lambda item: item.suggested_reduction, reverse=True)
    if cuts:
        st.markdown("#### Where to make adjustments first")
        for item in cuts[:5]:
            st.markdown(f"- **{item.name}:** reduce the monthly limit by **{money(item.suggested_reduction)}** "
                        f"(from {money(item.historical_average)} to {money(item.recommended_limit)}).")
    else:
        st.write("No category reductions are suggested from the recorded history and the income amount entered.")

with st.expander("How the recommendation is calculated"):
    st.markdown(
        "1. The planner reviews the five complete calendar months immediately before the selected effective month.\n"
        "2. It calculates average monthly income and average monthly expense for each category.\n"
        "3. Rent, food/groceries, utilities, education, healthcare and family are treated as essential by default.\n"
        "4. It subtracts your reserve and fixed commitments from the new income. Essential historical averages are protected first.\n"
        "5. If the remaining amount is too small, flexible categories are reduced proportionally. You can review and change these limits manually."
    )
    st.caption("This is a transparent planning estimate, not a prediction or a bank-linked financial recommendation. Category priority is based on category names and may not match every household.")


# Applying the plan is optional and requires explicit confirmation. It updates only
# category budgets in the selected effective month; it never edits transactions.
st.markdown("### Use this plan as your budgets")
applyable = [item for item in plan.categories if item.recommended_limit > 0]
zero_limit_count = len(plan.categories) - len(applyable)
existing_ids = {budget.category_id for budget, _ in existing_month_budgets}
will_update = sum(1 for item in applyable if item.category_id in existing_ids)
if plan.essential_shortfall > 0:
    st.warning("Resolve the essential-cost shortfall before applying this plan. The current income does not cover the historical essential averages plus your reserve and commitments.")
elif not applyable:
    st.caption("There are no positive category limits to apply. You can still download the report.")
else:
    st.caption(
        f"This will create or update {len(applyable)} category budget(s) for {month_label(effective_month)}. "
        f"{will_update} existing category budget(s) will be updated."
        + (f" {zero_limit_count} category limit(s) are zero and will be skipped because the budget system requires a positive limit." if zero_limit_count else "")
    )
    confirm_apply = st.checkbox(
        "I have reviewed the revised limits and want to apply them to this month's category budgets.",
        key=f"confirm_income_plan_apply_{effective_month}",
    )
    if st.button("Apply revised category budgets", type="primary", disabled=not confirm_apply,
                 key=f"apply_income_plan_{effective_month}"):
        try:
            with session_scope() as db:
                for item in applyable:
                    bs.upsert_budget(
                        db, profile.id, effective_month, paise_to_float(item.recommended_limit), item.category_id,
                        "Applied from the income-change spending plan",
                    )
        except Exception as exc:  # noqa: BLE001
            st.error(f"Could not apply the plan: {exc}")
        else:
            st.success(f"Applied {len(applyable)} revised category budget(s) for {month_label(effective_month)}. Your existing transactions were not changed.")

# Printable, single-page report (open the downloaded HTML and use Ctrl+P -> Save as PDF).
historical_by_id = {item.category_id: item for item in history.category_averages}
category_rows = "".join(
    "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
        html.escape(item.name), "Essential" if item.essential else "Flexible",
        html.escape(money(historical_by_id[item.category_id].total)), html.escape(money(item.historical_average)),
        html.escape(money(item.recommended_limit)), html.escape(money(item.suggested_reduction)),
    ) for item in plan.categories
)
monthly_rows = "".join(
    "<tr><td>{}</td><td>{}</td><td>{}</td></tr>".format(
        html.escape(month_label(month)), html.escape(money(history.income_by_month[month])),
        html.escape(money(history.expense_by_month[month])),
    ) for month in history.months
)
report_html = f'''<!doctype html><html><head><meta charset="utf-8"><title>Income Change Plan</title>
<style>
@page {{ size:A4; margin:12mm; }}
* {{ box-sizing:border-box; }} body {{ font-family:Arial,sans-serif; color:#172033; margin:0; font-size:10pt; }}
h1 {{ font-size:21pt; margin:0 0 4px; }} .muted {{ color:#64748b; }} .hero {{ background:#edf8f7; border:1px solid #ccebe5; border-radius:12px; padding:14px; margin:14px 0; }}
.grid {{ display:grid; grid-template-columns:repeat(3,1fr); gap:8px; margin:12px 0; }} .box {{ border:1px solid #dbe3ed; border-radius:9px; padding:9px; }} .label {{ font-size:8pt; color:#64748b; }} .value {{ font-size:14pt; font-weight:bold; margin-top:5px; }}
table {{ width:100%; border-collapse:collapse; margin-top:10px; }} th,td {{ padding:7px 6px; text-align:left; border-bottom:1px solid #e2e8f0; }} th {{ background:#f1f5f9; }} .foot {{ margin-top:14px; font-size:8pt; color:#64748b; }}
</style></head><body>
<h1>Income Change & Spending Plan</h1><div class="muted">Prepared for {html.escape(profile.full_name)} · Effective month: {html.escape(month_label(effective_month))}</div>
<div class="hero"><b>{'Income decrease entered' if plan.income_decreased else 'Monthly income scenario'}</b><p>Historical monthly income: <b>{html.escape(money(plan.baseline_income))}</b> · New monthly income: <b>{html.escape(money(plan.new_income))}</b></p>
<p>Change: <b>{html.escape(format_pct(plan.income_change_pct, 1, signed=True) if plan.income_change_pct is not None else 'No baseline available')}</b></p></div>
<div class="grid"><div class="box"><div class="label">Reserve</div><div class="value">{html.escape(money(plan.reserve))}</div></div>
<div class="box"><div class="label">Fixed commitments</div><div class="value">{html.escape(money(plan.commitments))}</div></div>
<div class="box"><div class="label">Recommended expense limits</div><div class="value">{html.escape(money(plan.recommended_expenses))}</div></div></div>
<h2>Five-month income and expense history</h2><table><thead><tr><th>Month</th><th>Income</th><th>Expenses</th></tr></thead><tbody>{monthly_rows}</tbody></table>
<h2>Category-by-category plan</h2><table><thead><tr><th>Category</th><th>Priority</th><th>Five-month total</th><th>Average / month</th><th>New limit</th><th>Reduction</th></tr></thead><tbody>{category_rows}</tbody></table>
<p><b>Monthly adjustment suggested:</b> {html.escape(money(plan.total_reduction))}</p>
<p><b>Remaining cushion:</b> {html.escape(money(plan.remaining_after_plan))}</p>
{f'<p style="color:#b91c1c"><b>Essential shortfall:</b> {html.escape(money(plan.essential_shortfall))}. Essential costs exceed the available expense capacity.</p>' if plan.essential_shortfall else ''}
<div class="foot">Based on recorded transactions for: {html.escape(', '.join(month_label(m) for m in history.months))}. Essential categories include Food and the configured essential category names. This report is a planning estimate, not financial advice. Review all suggested limits before using them.</div>
</body></html>'''

st.divider()
st.markdown("### Your one-page report")
st.download_button(
    "Download printable income-change report",
    data=report_html.encode("utf-8"),
    file_name=f"income_change_plan_{effective_month}.html",
    mime="text/html",
    type="primary",
    key=f"download_income_plan_{effective_month}",
)
st.caption("Open the downloaded HTML report in Chrome, press Ctrl + P, and choose ‘Save as PDF’ to keep a PDF copy.")
