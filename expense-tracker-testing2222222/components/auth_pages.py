"""Sign-in and registration pages (shown only to signed-out visitors)."""
from __future__ import annotations

import logging

import streamlit as st

import config
from components.layout import friendly_error, page_header
from database import session_scope
from services import auth_service

log = logging.getLogger(__name__)


def login_page() -> None:
    page_header("Welcome back", "Sign in to see your income, spending, budgets and goals.")
    with st.form("login_form"):
        email = st.text_input("Email address", key="login_email", autocomplete="email")
        password = st.text_input("Password", type="password", key="login_password")
        submitted = st.form_submit_button("Sign in", type="primary")
    if submitted:
        user_id = None
        try:
            with session_scope() as db:
                user_id = auth_service.authenticate(db, email, password).id
        except Exception as exc:  # noqa: BLE001 - shown safely by friendly_error
            friendly_error(exc)
        if user_id is not None:
            auth_service.start_session(st.session_state, user_id)
            st.rerun()
    st.caption("New here? Open **Create account** in the sidebar. Your data stays in a database on this computer.")


def register_page() -> None:
    page_header("Create your account", "Registration takes a minute. Default categories are set up for you.")
    with st.form("register_form"):
        name = st.text_input("Full name", key="reg_name", max_chars=100)
        email = st.text_input("Email address", key="reg_email", autocomplete="email")
        c1, c2 = st.columns(2)
        password = c1.text_input("Password", type="password", key="reg_password",
                                 help="At least 8 characters, with a letter and a number.")
        confirm = c2.text_input("Confirm password", type="password", key="reg_confirm")
        c3, c4 = st.columns(2)
        opening = c3.number_input("Opening balance (optional)", min_value=0.0, value=0.0, step=100.0, format="%.2f",
                                  key="reg_opening",
                                  help="Money you already have today. It is counted once and is not treated as income.")
        currency = c4.selectbox("Currency", list(config.CURRENCIES), key="reg_currency",
                                format_func=lambda c: f"{c} ({config.CURRENCIES[c]})",
                                help="Display only. Amounts are never converted.")
        submitted = st.form_submit_button("Create account", type="primary")
    st.caption("Balances shown in this app reflect only what you record here, not a live bank balance.")
    if submitted:
        user_id = None
        try:
            with session_scope() as db:
                user_id = auth_service.register_user(db, name, email, password, confirm, opening, currency).id
        except Exception as exc:  # noqa: BLE001
            friendly_error(exc)
        if user_id is not None:
            auth_service.start_session(st.session_state, user_id)
            st.session_state["_flash"] = [("success", "Account created. Welcome! Start by recording your income and expenses.")]
            st.rerun()
