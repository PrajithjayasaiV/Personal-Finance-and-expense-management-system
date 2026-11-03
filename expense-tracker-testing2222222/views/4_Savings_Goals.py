"""Savings goals: create goals, record contributions, see progress and projections."""
from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from components.forms import id_selectbox
from components.layout import empty_state, flash, friendly_error, metric_card, progress_bar, setup_page, show_table
from database import session_scope
from services import savings_service as sv
from utils.formatting import currency_symbol, escape_md, format_date, format_money, format_pct, widget_date_format
from utils.money import paise_to_float

profile = setup_page("Savings goals", "Set a target, record what you save, and see whether you are on track.")
today = date.today()
cur = profile.currency
sym = currency_symbol(cur)
datefmt = widget_date_format(profile.date_format)
money = lambda p, **kw: format_money(p, cur, **kw)  # noqa: E731
STATUS_TEXT = {"active": "Active", "completed": "Completed", "paused": "Paused"}
TRACK_TONE = {sv.COMPLETED: "positive", sv.ON_TRACK: "positive", sv.BEHIND: "warning", sv.OVERDUE: "negative",
              sv.NO_PACE: "warning", sv.NO_DEADLINE: "neutral", sv.PAUSED: "neutral"}


def run_action(action, success_message: str) -> None:
    try:
        with session_scope() as db:
            action(db)
    except Exception as exc:  # noqa: BLE001
        friendly_error(exc)
        return
    flash("success", success_message)
    st.rerun()


try:
    with session_scope() as db:
        progress_list = sv.list_progress(db, profile.id, today)
        contributions = {p.goal_id: [(c.id, c.contribution_date, c.amount, c.note) for c in sv.list_contributions(db, profile.id, p.goal_id)]
                         for p in progress_list}
except Exception as exc:  # noqa: BLE001
    friendly_error(exc)
    st.stop()

tab_goals, tab_new = st.tabs(["My goals", "New goal"])

# ====================================================================== new goal
with tab_new:
    with st.form("new_goal", clear_on_submit=True):
        name = st.text_input("Goal name", max_chars=100, placeholder="Emergency fund, new laptop, trip...", key="ng_name")
        description = st.text_area("Description (optional)", max_chars=500, key="ng_desc")
        g1, g2 = st.columns(2)
        target = g1.number_input(f"Target amount ({sym})", min_value=0.01, value=None, step=1000.0, format="%.2f", placeholder="0.00", key="ng_target")
        saved = g2.number_input(f"Already saved ({sym}, optional)", min_value=0.0, value=0.0, step=500.0, format="%.2f", key="ng_saved",
                                help="Recorded as a first contribution so your history stays complete.")
        g3, g4 = st.columns(2)
        start = g3.date_input("Start date", value=today, format=datefmt, key="ng_start")
        has_deadline = g4.checkbox("Set a target date", key="ng_has_deadline")
        deadline = g4.date_input("Target date", value=date(today.year + 1, today.month, min(today.day, 28)), format=datefmt,
                                 key="ng_deadline", disabled=not has_deadline)
        create = st.form_submit_button("Create goal", type="primary")
    if create:
        run_action(lambda db: sv.create_goal(db, profile.id, name=name, target_amount=target, description=description, start_date=start,
                                             target_date=deadline if has_deadline else None, initial_saved=saved, today=today),
                   "Goal created.")

# ====================================================================== goals
with tab_goals:
    if not progress_list:
        empty_state("No savings goals yet", "Open the New goal tab to create one. Contributions you record will drive the progress shown here.", "🐖")
    else:
        show = st.selectbox("Show", ["All goals", "Active", "Completed", "Paused"], key="goal_filter")
        wanted = {"All goals": None, "Active": "active", "Completed": "completed", "Paused": "paused"}[show]
        visible = [p for p in progress_list if wanted is None or p.status == wanted]
        if not visible:
            st.info("No goals with that status.")
    for p in (visible if progress_list else []):
        gid = p.goal_id
        with st.container(border=True):
            st.markdown(f"### {escape_md(p.name)}  \n*{STATUS_TEXT[p.status]}*")
            if p.description:
                st.write(escape_md(p.description))
            progress_bar(p.display_pct, "positive" if p.status == "completed" else "accent")
            m1, m2, m3, m4 = st.columns(4)
            with m1:
                metric_card("Target", money(p.target), f"Started {format_date(p.start_date, profile.date_format)}", "neutral")
            with m2:
                metric_card("Saved", money(p.saved), f"{format_pct(p.pct, 1)} complete" + (f" ({money(p.over_target)} over target)" if p.over_target else ""), "accent")
            with m3:
                metric_card("Remaining", money(p.remaining), "Nothing left to save" if p.remaining == 0 else "Still to save", "neutral")
            with m4:
                if p.target_date:
                    when = f"{p.days_left} day(s) left" if p.days_left is not None and p.days_left >= 0 else f"{abs(p.days_left or 0)} day(s) overdue"
                    metric_card("Target date", format_date(p.target_date, profile.date_format), when, "negative" if (p.days_left or 0) < 0 and p.remaining else "neutral")
                else:
                    metric_card("Target date", "Not set", "No deadline", "neutral")

            st.markdown(f"**{'✔' if p.track_status in (sv.COMPLETED, sv.ON_TRACK) else '▲'} {escape_md(p.track_message)}**")
            facts = []
            if p.required_monthly:
                facts.append(f"To finish by the target date you would need about **{money(p.required_monthly)} per month** (about {money(p.required_weekly)} per week).")
            if p.remaining and p.monthly_pace:
                facts.append(f"Your pace over the last 90 days is about **{money(p.monthly_pace)} per month**"
                             + (f", which projects completion around **{format_date(p.projected_completion, profile.date_format)}**." if p.projected_completion else "."))
            elif p.remaining and p.status == "active":
                facts.append("No contributions in the last 90 days, so no completion date can be projected.")
            for f in facts:
                st.markdown(f)

            if p.remaining and p.status != "paused":
                with st.expander("What if I change my monthly saving?"):
                    default = float(max(paise_to_float(p.required_monthly or 0), paise_to_float(p.monthly_pace), 100.0))
                    what_if = st.number_input(f"Monthly contribution ({sym})", min_value=0.0, value=round(default, 2), step=100.0,
                                              format="%.2f", key=f"whatif_{gid}")
                    projected = sv.project_completion(p.remaining, int(round(what_if * 100)), today)
                    if projected is None:
                        st.write("Enter an amount above zero to see a projected date.")
                    else:
                        line = f"Saving {money(int(round(what_if * 100)))} per month, you would finish around **{format_date(projected, profile.date_format)}**."
                        if p.target_date:
                            line += " That is within your target date." if projected <= p.target_date else " That is after your target date."
                        st.write(line)
                    st.caption("Projections are estimates based on steady monthly saving. They are not guaranteed outcomes.")

            with st.expander("Add a contribution"):
                with st.form(f"contrib_{gid}", clear_on_submit=True):
                    k1, k2, k3 = st.columns([1, 1, 2])
                    amt = k1.number_input(f"Amount ({sym})", min_value=0.01, value=None, step=100.0, format="%.2f", placeholder="0.00", key=f"c_amt_{gid}")
                    dt = k2.date_input("Date", value=today, max_value=today, format=datefmt, key=f"c_date_{gid}")
                    nt = k3.text_input("Note (optional)", max_chars=255, key=f"c_note_{gid}")
                    add = st.form_submit_button("Add contribution", type="primary")
                st.caption("A contribution is a savings record. It does not change your income, expenses or recorded balance.")
                if add:
                    run_action(lambda db: sv.add_contribution(db, profile.id, gid, amt, dt, nt, today=today), "Contribution recorded.")

            rows = contributions.get(gid, [])
            with st.expander(f"Contribution history ({len(rows)})"):
                if not rows:
                    st.write("No contributions recorded yet.")
                else:
                    show_table(pd.DataFrame([{"Date": format_date(d, profile.date_format), "Amount": money(a), "Note": n} for _, d, a, n in rows]), hide_index=True)
                    pick = id_selectbox("Edit or delete a contribution",
                                        [(cid, f"{format_date(d, profile.date_format)} - {money(a)}" + (f" - {n}" if n else "")) for cid, d, a, n in rows],
                                        f"c_pick_{gid}")
                    chosen = next(r for r in rows if r[0] == pick)
                    with st.form(f"c_edit_{gid}_{pick}"):
                        e1, e2, e3 = st.columns([1, 1, 2])
                        new_amt = e1.number_input("Amount", min_value=0.01, value=paise_to_float(chosen[2]), step=100.0, format="%.2f", key=f"ce_amt_{pick}")
                        new_dt = e2.date_input("Date", value=chosen[1], max_value=today, format=datefmt, key=f"ce_dt_{pick}")
                        new_nt = e3.text_input("Note", value=chosen[3], max_chars=255, key=f"ce_nt_{pick}")
                        save = st.form_submit_button("Save contribution")
                    if save:
                        run_action(lambda db: sv.update_contribution(db, profile.id, pick, new_amt, new_dt, new_nt, today=today), "Contribution updated.")
                    ck = f"del_contrib_{pick}"
                    if st.session_state.get(ck):
                        st.warning("Delete this contribution? Progress will be recalculated.")
                        y, n_, _ = st.columns([2, 1, 4])
                        if y.button("Yes, delete", type="primary", key=f"{ck}_yes"):
                            st.session_state.pop(ck, None)
                            run_action(lambda db: sv.delete_contribution(db, profile.id, pick), "Contribution deleted.")
                        if n_.button("Cancel", key=f"{ck}_no"):
                            st.session_state.pop(ck, None)
                            st.rerun()
                    elif st.button("Delete contribution", key=f"{ck}_ask"):
                        st.session_state[ck] = True
                        st.rerun()

            with st.expander("Edit or manage this goal"):
                with st.form(f"goal_edit_{gid}"):
                    nm = st.text_input("Goal name", value=p.name, max_chars=100, key=f"ge_name_{gid}")
                    ds = st.text_area("Description", value=p.description, max_chars=500, key=f"ge_desc_{gid}")
                    t1, t2, t3 = st.columns(3)
                    tg = t1.number_input(f"Target amount ({sym})", min_value=0.01, value=paise_to_float(p.target), step=1000.0, format="%.2f", key=f"ge_tg_{gid}")
                    sd = t2.date_input("Start date", value=p.start_date, format=datefmt, key=f"ge_sd_{gid}")
                    keep = t3.checkbox("Has a target date", value=p.target_date is not None, key=f"ge_has_{gid}")
                    td = t3.date_input("Target date", value=p.target_date or date(today.year + 1, today.month, min(today.day, 28)), format=datefmt,
                                       key=f"ge_td_{gid}", disabled=not keep)
                    save_goal = st.form_submit_button("Save goal", type="primary")
                if save_goal:
                    run_action(lambda db: sv.update_goal(db, profile.id, gid, name=nm, target_amount=tg, description=ds, start_date=sd,
                                                         target_date=td if keep else None), "Goal updated.")
                if p.status != "completed":
                    paused = p.status == "paused"
                    if st.button("Resume this goal" if paused else "Pause this goal", key=f"pause_{gid}"):
                        run_action(lambda db: sv.set_goal_paused(db, profile.id, gid, not paused), "Goal resumed." if paused else "Goal paused.")
                gk = f"del_goal_{gid}"
                if st.session_state.get(gk):
                    st.warning(f"Delete '{p.name}' and all {len(rows)} of its contributions? This cannot be undone.")
                    y, n_, _ = st.columns([2, 1, 4])
                    if y.button("Yes, delete goal", type="primary", key=f"{gk}_yes"):
                        st.session_state.pop(gk, None)
                        run_action(lambda db: sv.delete_goal(db, profile.id, gid), "Goal deleted.")
                    if n_.button("Cancel", key=f"{gk}_no"):
                        st.session_state.pop(gk, None)
                        st.rerun()
                elif st.button("Delete this goal", key=f"{gk}_ask"):
                    st.session_state[gk] = True
                    st.rerun()
