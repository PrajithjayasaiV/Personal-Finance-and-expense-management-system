"""Authentication gate, navigation menu and sidebar."""
from __future__ import annotations

import logging

import streamlit as st

import config
from components.auth_pages import login_page, register_page
from components.layout import inject_css
from database import session_scope
from services import auth_service, notification_service
from services.auth_service import UserProfile
from utils.formatting import escape_html
from utils.errors import AuthenticationError

log = logging.getLogger(__name__)

# (script path, title, icon, url path)
APP_PAGES = [
    ("views/1_Dashboard.py", "Dashboard", "📊", "dashboard"),
    ("views/2_Transactions.py", "Transactions", "💳", "transactions"),
    ("views/3_Budgets.py", "Budgets", "🎯", "budgets"),
    ("views/9_Income_Change_Plan.py", "Income change plan", "🧭", "income-change-plan"),
    ("views/4_Savings_Goals.py", "Savings goals", "🐖", "savings-goals"),
    ("views/5_Analytics.py", "Analytics", "📈", "analytics"),
    ("views/6_Reports.py", "Reports", "🧾", "reports"),
    ("views/7_Settings.py", "Settings", "⚙️", "settings"),
    ("views/8_Notifications.py", "Notifications", "🔔", "notifications"),
]


def current_profile() -> UserProfile | None:
    """The signed-in user, re-validated against the database on every run (never trusts session state alone)."""
    if auth_service.session_user_id(st.session_state) is None:
        return None
    try:
        with session_scope() as db:
            return auth_service.to_profile(auth_service.get_authenticated_user(db, st.session_state))
    except AuthenticationError:
        return None


def require_login() -> UserProfile:
    """Call at the top of every protected page."""
    profile = current_profile()
    if profile is None:
        st.warning("Please sign in to continue.")
        st.stop()
    return profile


def logout() -> None:
    auth_service.end_session(st.session_state)
    st.rerun()


def run_navigation() -> None:
    """Build the menu for the current auth state and run the selected page."""
    profile = current_profile()
    if profile is None:
        inject_css()
        st.navigation([
            st.Page(login_page, title="Sign in", icon="🔐", url_path="login", default=True),
            st.Page(register_page, title="Create account", icon="📝", url_path="register"),
        ]).run()
        with st.sidebar:
            st.markdown(
                f'<div class="sf-brand"><div class="sf-brand-mark">₹</div>'
                f'<div><div class="sf-brand-name">{escape_html(config.APP_BRAND)}</div>'
                '<div class="sf-brand-caption">PERSONAL FINANCE</div></div></div>',
                unsafe_allow_html=True,
            )
            st.caption("Track spending, budgets and savings goals locally.")
        return

    try:
        with session_scope() as db:
            unread = notification_service.unread_count(db, profile.id)
    except Exception:  # noqa: BLE001 - a badge must never break navigation
        log.exception("Could not load notification count")
        unread = 0

    pages = []
    for path, title, icon, url in APP_PAGES:
        if title == "Notifications" and unread:
            title = f"Notifications ({unread})"
        pages.append(st.Page(path, title=title, icon=icon, url_path=url, default=(url == "dashboard")))
    nav = st.navigation(pages)
    with st.sidebar:
        st.markdown(
            f'<div class="sf-brand"><div class="sf-brand-mark">₹</div>'
            f'<div><div class="sf-brand-name">{escape_html(config.APP_BRAND)}</div>'
            '<div class="sf-brand-caption">PERSONAL FINANCE</div></div></div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<div class="sf-user-card"><div class="sf-user-label">SIGNED IN AS</div>'
            f'<div class="sf-user-name">{escape_html(profile.full_name)}</div></div>',
            unsafe_allow_html=True,
        )
        if st.button("Log out", key="logout_btn"):
            logout()
    nav.run()
