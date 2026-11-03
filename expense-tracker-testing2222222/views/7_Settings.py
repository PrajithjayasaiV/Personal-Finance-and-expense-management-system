"""Settings: profile, preferences, balances, notifications, password and hidden insights."""
from __future__ import annotations

from datetime import date

import streamlit as st

import config
from components.layout import flash, friendly_error, note, setup_page
from database import session_scope
from services import auth_service
from services import insights_service as ins
from utils.dates import DASHBOARD_PRESETS, PRESET_LABELS
from utils.formatting import currency_symbol
from utils.money import paise_to_float

profile = setup_page("Settings", "Your profile, display preferences and the amounts used for safe-to-spend.")
sym = currency_symbol(profile.currency)
today = date.today()


def save(action, success_message: str) -> None:
    try:
        with session_scope() as db:
            action(db)
    except Exception as exc:  # noqa: BLE001
        friendly_error(exc)
        return
    flash("success", success_message)
    st.rerun()


# ------------------------------------------------------------------ profile and display
st.subheader("Profile and display")
with st.form("settings_profile"):
    name = st.text_input("Full name", value=profile.full_name, max_chars=100, key="set_name")
    st.text_input("Email address", value=profile.email, disabled=True, help="Your email is your sign-in name and cannot be changed here.")
    c1, c2, c3 = st.columns(3)
    currencies = list(config.CURRENCIES)
    currency = c1.selectbox("Display currency", currencies, index=currencies.index(profile.currency) if profile.currency in currencies else 0,
                            format_func=lambda c: f"{c} ({config.CURRENCIES[c]})", key="set_currency")
    formats = {f"{label}  (e.g. {today.strftime(fmt)})": fmt for label, fmt in config.DATE_FORMATS.items()}
    fmt_labels = list(formats)
    current_fmt = next((i for i, f in enumerate(formats.values()) if f == profile.date_format), 0)
    date_label = c2.selectbox("Date format", fmt_labels, index=current_fmt, key="set_datefmt")
    ranges = list(DASHBOARD_PRESETS[:-1])
    default_range = c3.selectbox("Default dashboard range", ranges, index=ranges.index(profile.default_range) if profile.default_range in ranges else 0,
                                 format_func=PRESET_LABELS.get, key="set_range")
    st.caption("Changing the currency only changes the symbol shown. Existing amounts are not converted using exchange rates.")
    st.markdown("**Notifications** (shown inside the app)")
    n1, n2, n3 = st.columns(3)
    notify_budget = n1.checkbox("Budget alerts", value=profile.notify_budget, key="set_n_budget")
    notify_goals = n2.checkbox("Savings goal alerts", value=profile.notify_goals, key="set_n_goals")
    notify_insights = n3.checkbox("Spending insight alerts", value=profile.notify_insights, key="set_n_insights")
    saved = st.form_submit_button("Save preferences", type="primary")
if saved:
    save(lambda db: auth_service.update_settings(db, profile.id, full_name=name, currency=currency, date_format=formats[date_label],
                                                 default_range=default_range, notify_budget=notify_budget, notify_goals=notify_goals,
                                                 notify_insights=notify_insights), "Preferences saved.")

# ------------------------------------------------------------------ money
st.subheader("Balance and safe-to-spend")
with st.form("settings_money"):
    m1, m2, m3 = st.columns(3)
    opening = m1.number_input(f"Opening balance ({sym})", min_value=0.0, value=paise_to_float(profile.opening_balance), step=100.0, format="%.2f",
                              key="set_opening", help="Money you had when you started using the app. It is counted once and is never treated as income.")
    reserve = m2.number_input(f"Minimum reserve ({sym})", min_value=0.0, value=paise_to_float(profile.min_reserve), step=100.0, format="%.2f",
                              key="set_reserve", help="An amount you want to keep untouched. Safe-to-spend subtracts it.")
    commitments = m3.number_input(f"Upcoming commitments ({sym})", min_value=0.0, value=paise_to_float(profile.upcoming_commitments), step=100.0,
                                  format="%.2f", key="set_commitments",
                                  help="Bills you know you must pay that are not recorded yet. Clear this after recording them so they are not counted twice.")
    saved_money = st.form_submit_button("Save amounts", type="primary")
note("Your balance reflects only what you record in this app. It is not a live bank balance. Unrecorded income, expenses and pending "
     "payments are not known, so treat safe-to-spend as an estimate.")
if saved_money:
    save(lambda db: (auth_service.update_opening_balance(db, profile.id, opening),
                     auth_service.update_settings(db, profile.id, min_reserve=reserve, upcoming_commitments=commitments)), "Amounts saved.")

# ------------------------------------------------------------------ password
st.subheader("Change password")
with st.form("settings_password", clear_on_submit=True):
    current = st.text_input("Current password", type="password", key="pw_current")
    p1, p2 = st.columns(2)
    new = p1.text_input("New password", type="password", key="pw_new", help="At least 8 characters with a letter and a number.")
    confirm = p2.text_input("Confirm new password", type="password", key="pw_confirm")
    changed = st.form_submit_button("Change password")
if changed:
    save(lambda db: auth_service.change_password(db, profile.id, current, new, confirm), "Password changed.")

# ------------------------------------------------------------------ insights and data
st.subheader("Insights and your data")
if st.button("Show dismissed insights again", key="restore_insights"):
    def restore(db):
        n = ins.clear_feedback(db, profile.id)
        flash("info", f"{n} hidden insight(s) restored." if n else "You have no hidden insights.")
    try:
        with session_scope() as db:
            restore(db)
    except Exception as exc:  # noqa: BLE001
        friendly_error(exc)
    else:
        st.rerun()
st.caption(f"Your data is stored in a local SQLite file: {config.DB_PATH}. Back up that file to keep a copy of your records.")
