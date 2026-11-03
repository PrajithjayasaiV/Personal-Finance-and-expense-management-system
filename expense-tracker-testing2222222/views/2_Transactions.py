"""Transactions: add, search, filter, edit, delete and manage categories."""
from __future__ import annotations

import math
from datetime import date

import pandas as pd
import streamlit as st

import config
from components.forms import date_range_selector, id_selectbox, transaction_form
from components.layout import empty_state, flash, friendly_error, setup_page, show_table
from database import session_scope
from services import transaction_service as ts
from utils.formatting import escape_md, format_date, format_money
from utils.money import paise_to_float

profile = setup_page("Transactions", "Record income and expenses, then search, filter and edit them.")
today = date.today()
cur = profile.currency
money = lambda p, **kw: format_money(p, cur, **kw)  # noqa: E731


def load_categories() -> list[tuple[int, str, str]]:
    with session_scope() as db:
        return [(c.id, c.name, c.type) for c in ts.list_categories(db, profile.id)]


def run_action(action, success_message: str, *, bump_table: bool = False) -> bool:
    """Run a service call in its own transaction. On success: flash, optionally reset the table, rerun."""
    try:
        with session_scope() as db:
            action(db)
    except Exception as exc:  # noqa: BLE001
        friendly_error(exc)
        return False
    flash("success", success_message)
    if bump_table:
        st.session_state["tl_version"] = st.session_state.get("tl_version", 0) + 1
    st.rerun()
    return True


categories = load_categories()
tab_add, tab_list, tab_cat = st.tabs(["Add transaction", "All transactions", "Categories"])

# ====================================================================== add
with tab_add:
    values = transaction_form("add", profile, categories, submit_label="Add transaction", clear_on_submit=True, today=today)
    if values:
        run_action(lambda db: ts.create_transaction(db, profile.id, **values), "Transaction saved.")
    st.caption("Dates cannot be in the future. Amounts are stored exactly, to the paisa.")

# ====================================================================== list / edit / delete
with tab_list:
    cat_labels = [(cid, f"{name} ({kind})") for cid, name, kind in categories]
    f1, f2, f3 = st.columns([2, 1, 1])
    search = f1.text_input("Search", placeholder="Description or category", key="tl_search")
    type_pick = f2.selectbox("Type", ["All", "Income", "Expense"], key="tl_type")
    method_pick = f3.selectbox("Payment method", ["All", *config.PAYMENT_METHODS], key="tl_method")
    g1, g2 = st.columns([1, 1])
    with g1:
        cat_pick = id_selectbox("Category", [(i, n) for i, n in cat_labels if type_pick == "All" or n.endswith(f"({type_pick.lower()})")],
                                "tl_category", allow_all="All categories")
    with g2:
        rng = date_range_selector("tl", profile, presets=("all_time", "current_month", "previous_month", "last_30_days",
                                                         "last_90_days", "current_year", "custom"), default="all_time", today=today)
    h1, h2, h3 = st.columns([2, 1, 1])
    sort_options = {"Date (newest first)": ("date", True), "Date (oldest first)": ("date", False),
                    "Amount (highest first)": ("amount", True), "Amount (lowest first)": ("amount", False)}
    sort_label = h1.selectbox("Sort by", list(sort_options), key="tl_sort")
    page_size = h2.selectbox("Rows per page", config.PAGE_SIZE_OPTIONS, index=1, key="tl_size")

    def reset_filters() -> None:
        for k in ("tl_search", "tl_type", "tl_method", "tl_category", "tl_range_preset", "tl_sort", "tl_size"):
            st.session_state.pop(k, None)

    h3.markdown("&nbsp;", unsafe_allow_html=True)
    h3.button("Reset filters", on_click=reset_filters, key="tl_reset")

    filters = ts.TransactionFilters(start=rng.start, end=rng.end, type=None if type_pick == "All" else type_pick.lower(),
                                    category_id=cat_pick, payment_method=None if method_pick == "All" else method_pick, search=search)
    signature = (search, type_pick, method_pick, cat_pick, rng.start, rng.end, sort_label, page_size)
    if st.session_state.get("tl_sig") != signature:        # any filter change returns to page 1
        st.session_state["tl_sig"] = signature
        st.session_state["tl_page"] = 1
    sort_by, descending = sort_options[sort_label]

    try:
        with session_scope() as db:
            _, total = ts.query_transactions(db, profile.id, filters, limit=1)
            pages = max(1, math.ceil(total / page_size))
            page = min(max(1, st.session_state.get("tl_page", 1)), pages)
            st.session_state["tl_page"] = page
            rows, _ = ts.query_transactions(db, profile.id, filters, sort_by=sort_by, descending=descending,
                                            limit=page_size, offset=(page - 1) * page_size)
            all_count = ts.count_transactions(db, profile.id)
    except Exception as exc:  # noqa: BLE001
        friendly_error(exc)
        st.stop()

    if all_count == 0:
        empty_state("No transactions yet", "Use the Add transaction tab to record your first income or expense.", "💳")
    elif total == 0:
        empty_state("No transactions match these filters", "Try a different search or date range, or press Reset filters.", "🔍")
    else:
        def go_to(n: int) -> None:
            st.session_state["tl_page"] = n

        n1, n2, n3 = st.columns([1, 3, 1])
        n1.button("Previous", disabled=page <= 1, on_click=go_to, args=(page - 1,), key="tl_prev")
        n2.markdown(f"Page **{page}** of **{pages}** - {total} matching transaction(s)")
        n3.button("Next", disabled=page >= pages, on_click=go_to, args=(page + 1,), key="tl_next")

        df = pd.DataFrame([{
            "Date": format_date(r.txn_date, profile.date_format), "Type": r.type.capitalize(), "Category": r.category_name,
            "Description": r.description, "Payment method": r.payment_method,
            "Amount": money(r.amount if r.type == "income" else -r.amount, signed=True),
        } for r in rows])
        show_table(df, hide_index=True)

        version = st.session_state.get("tl_version", 0)
        picked = id_selectbox(
            "Select a transaction to edit or delete",
            [(r.id, f"{format_date(r.txn_date, profile.date_format)} | {r.category_name} | "
                    f"{money(r.amount if r.type == 'income' else -r.amount, signed=True)}"
                    + (f" | {r.description[:40]}" if r.description else "")) for r in rows],
            f"tl_pick_{version}", allow_all="Choose a transaction from this page...")
        row = next((r for r in rows if r.id == picked), None)
        if row is not None:
            st.divider()
            st.markdown(f"#### Edit or delete: {escape_md(row.category_name)} on {format_date(row.txn_date, profile.date_format)}")
            defaults = dict(type=row.type, amount=paise_to_float(row.amount), date=row.txn_date, category_id=row.category_id,
                            payment_method=row.payment_method, description=row.description)
            edits = transaction_form(f"edit_{row.id}", profile, categories, defaults=defaults, submit_label="Save changes", today=today)
            if edits:
                run_action(lambda db: ts.update_transaction(db, profile.id, row.id, **edits), "Transaction updated.", bump_table=True)
            confirm_key = f"del_confirm_{row.id}"
            if st.session_state.get(confirm_key):
                st.warning("This permanently deletes the transaction. Budgets, totals and charts will update. This cannot be undone.")
                d1, d2, _ = st.columns([2, 1, 4])
                if d1.button("Yes, delete permanently", type="primary", key=f"del_yes_{row.id}"):
                    st.session_state.pop(confirm_key, None)
                    run_action(lambda db: ts.delete_transaction(db, profile.id, row.id), "Transaction deleted.", bump_table=True)
                if d2.button("Cancel", key=f"del_no_{row.id}"):
                    st.session_state.pop(confirm_key, None)
                    st.rerun()
            elif st.button("Delete this transaction", key=f"del_{row.id}"):
                st.session_state[confirm_key] = True
                st.rerun()

# ====================================================================== categories
with tab_cat:
    try:
        with session_scope() as db:
            usage = ts.category_usage_counts(db, profile.id)
            cat_rows = [(c.id, c.name, c.type, c.is_default) for c in ts.list_categories(db, profile.id)]
    except Exception as exc:  # noqa: BLE001
        friendly_error(exc)
        st.stop()

    col_in, col_ex = st.columns(2)
    for col, kind in ((col_in, "income"), (col_ex, "expense")):
        with col:
            st.markdown(f"**{kind.capitalize()} categories**")
            show_table(pd.DataFrame([{"Category": n, "Transactions": usage.get(i, 0), "Origin": "Default" if d else "Custom"}
                                     for i, n, t, d in cat_rows if t == kind]), hide_index=True)

    st.markdown("#### Add a category")
    with st.form("category_creation_form", clear_on_submit=True):
        a1, a2 = st.columns([2, 1])
        new_name = a1.text_input("Category name", max_chars=50, key="newcat_name")
        new_type = a2.radio("Type", ["Expense", "Income"], horizontal=True, key="newcat_type")
        add_cat = st.form_submit_button("Add category", type="primary")
    if add_cat:
        run_action(lambda db: ts.create_category(db, profile.id, new_name, new_type.lower()), "Category added.")

    st.markdown("#### Rename or delete a category")
    chosen = id_selectbox("Category", [(i, f"{n} ({t})") for i, n, t, _ in cat_rows], "managecat")
    if chosen is not None:
        name, kind = next((n, t) for i, n, t, _ in cat_rows if i == chosen)
        used = usage.get(chosen, 0)
        with st.form(f"rename_{chosen}"):
            renamed = st.text_input("New name", value=name, max_chars=50, key=f"rename_name_{chosen}")
            do_rename = st.form_submit_button("Rename")
        if do_rename:
            run_action(lambda db: ts.rename_category(db, profile.id, chosen, renamed), "Category renamed. Existing transactions keep their category.")
        st.markdown(f"**Delete '{escape_md(name)}'** - used by {used} transaction(s).")
        target = None
        if used:
            st.caption("Transactions are never erased silently. Choose where they should move. Budgets set on this category are removed with it.")
            target = id_selectbox("Move transactions to", [(i, n) for i, n, t, _ in cat_rows if t == kind and i != chosen], f"movecat_{chosen}")
        sure = st.checkbox("I understand this cannot be undone", key=f"delcat_ok_{chosen}")
        if st.button("Delete category", disabled=not sure or (used > 0 and target is None), key=f"delcat_{chosen}"):
            run_action(lambda db: ts.delete_category(db, profile.id, chosen, reassign_to_id=target), "Category deleted.", bump_table=True)
