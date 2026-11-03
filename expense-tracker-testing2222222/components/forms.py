"""Reusable form helpers: date ranges, month pickers, category pickers."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import streamlit as st

import config
from services.auth_service import UserProfile
from utils.dates import DASHBOARD_PRESETS, PRESET_LABELS, month_key, month_label, resolve_preset, shift_month
from utils.formatting import currency_symbol, format_range, widget_date_format


@dataclass(frozen=True)
class DateRange:
    preset: str
    start: date | None   # None for "all time"
    end: date | None
    label: str


def date_range_selector(key: str, profile: UserProfile, *, presets: tuple[str, ...] = DASHBOARD_PRESETS,
                        default: str | None = None, today: date | None = None) -> DateRange:
    """Preset picker with an optional custom range. Stops the page on an invalid custom range."""
    today = today or date.today()
    wanted = default or profile.default_range
    index = presets.index(wanted) if wanted in presets else 0
    preset = st.selectbox("Date range", presets, index=index, key=f"{key}_preset", format_func=PRESET_LABELS.get)
    if preset == "custom":
        c1, c2 = st.columns(2)
        fmt = widget_date_format(profile.date_format)
        start = c1.date_input("From", value=today.replace(day=1), max_value=today, key=f"{key}_from", format=fmt)
        end = c2.date_input("To", value=today, max_value=today, key=f"{key}_to", format=fmt)
        if not isinstance(start, date) or not isinstance(end, date):
            st.info("Choose both a start and an end date.")
            st.stop()
        if end < start:
            st.error("The end date must not be earlier than the start date.")
            st.stop()
    else:
        start, end = resolve_preset(preset, today)
    return DateRange(preset, start, end, format_range(start, end, profile.date_format))


def month_selector(key: str, label: str = "Month", *, today: date | None = None, back: int = 24, forward: int = 12,
                   default: str | None = None) -> str:
    """Select a calendar month; returns a 'YYYY-MM' key."""
    current = month_key(today or date.today())
    options = [shift_month(current, n) for n in range(-back, forward + 1)]
    start = default if default in options else current
    return st.selectbox(label, options, index=options.index(start), key=key, format_func=month_label)


def id_selectbox(label: str, items: list[tuple[int, str]], key: str, *, index: int = 0, allow_all: str | None = None,
                 **kwargs) -> int | None:
    """Selectbox over ``(id, name)`` pairs returning the id (``None`` for the optional 'all' entry)."""
    options: list[int | None] = ([None] if allow_all else []) + [i for i, _ in items]
    names = {i: n for i, n in items}
    names[None] = allow_all or ""
    if not options:
        st.selectbox(label, ["(none available)"], disabled=True, key=f"{key}_empty")
        return None
    return st.selectbox(label, options, index=min(index, len(options) - 1), key=key, format_func=names.get, **kwargs)


def transaction_form(key: str, profile: UserProfile, categories: list[tuple[int, str, str]], *, defaults: dict | None = None,
                     submit_label: str = "Save transaction", clear_on_submit: bool = False, today: date | None = None) -> dict | None:
    """Income/expense form. Returns the submitted values (not yet validated) or ``None``.

    The type switch sits outside the form so the category list updates immediately.
    Validation happens in the service layer, not here.
    """
    today = today or date.today()
    d = defaults or {}
    kind = st.radio("Transaction type", ["Expense", "Income"], index=0 if d.get("type", "expense") == "expense" else 1,
                    horizontal=True, key=f"{key}_type")
    txn_type = kind.lower()
    choices = [(cid, name) for cid, name, t in categories if t == txn_type]
    if not choices:
        st.warning(f"You have no {txn_type} categories yet. Add one on the Categories tab first.")
    ids = [cid for cid, _ in choices]
    names = dict(choices)
    with st.form(f"{key}_form", clear_on_submit=clear_on_submit):
        c1, c2 = st.columns(2)
        amount = c1.number_input(f"Amount ({currency_symbol(profile.currency)})", min_value=0.01, value=d.get("amount"), step=1.0,
                                 format="%.2f", placeholder="0.00", key=f"{key}_amount")
        when = c2.date_input("Date", value=d.get("date", today), min_value=date(2000, 1, 1), max_value=today,
                             format=widget_date_format(profile.date_format), key=f"{key}_date")
        c3, c4 = st.columns(2)
        cat_index = ids.index(d["category_id"]) if d.get("category_id") in ids else 0
        category = c3.selectbox("Category", ids, index=cat_index if ids else None, format_func=names.get, key=f"{key}_category",
                                disabled=not ids)
        methods = list(config.PAYMENT_METHODS)
        method = c4.selectbox("Payment method", methods, index=methods.index(d.get("payment_method", "UPI")) if d.get("payment_method", "UPI") in methods else 0,
                              key=f"{key}_method", help="A label you choose. The app is not connected to any bank, card or UPI service.")
        description = st.text_input("Description or notes (optional)", value=d.get("description", ""), max_chars=255, key=f"{key}_desc")
        submitted = st.form_submit_button(submit_label, type="primary")
    if not submitted:
        return None
    return dict(txn_type=txn_type, amount=amount, category_id=category, txn_date=when, payment_method=method, description=description)
