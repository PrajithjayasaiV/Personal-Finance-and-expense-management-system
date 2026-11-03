"""Notifications: budget alerts, goal milestones and deadlines, and spending insights."""
from __future__ import annotations

from datetime import date

import streamlit as st

from components.layout import SEVERITY_ICON, empty_state, friendly_error, page_link, setup_page
from database import session_scope
from services import notification_service as ns
from utils.formatting import escape_md, format_datetime

profile = setup_page("Notifications", "Alerts about budgets, savings goals and spending. They stay inside this app.")

DESTINATIONS = {
    "budget": ("views/3_Budgets.py", "Open budgets", "🎯"),
    "goal": ("views/4_Savings_Goals.py", "Open savings goals", "🐖"),
    "transaction": ("views/2_Transactions.py", "Open transactions", "💳"),
    "insight": ("views/1_Dashboard.py", "Open dashboard", "📊"),
}

try:
    with session_scope() as db:
        ns.refresh_all(db, profile.id, date.today())   # re-evaluate conditions before listing
except Exception as exc:  # noqa: BLE001
    friendly_error(exc)

view = st.radio("Show", ["Unread", "All current alerts", "Include resolved alerts"], horizontal=True, key="notif_view")
try:
    with session_scope() as db:
        items = ns.list_notifications(db, profile.id, unread_only=view == "Unread", include_inactive=view == "Include resolved alerts")
        unread = ns.unread_count(db, profile.id)
except Exception as exc:  # noqa: BLE001
    friendly_error(exc)
    st.stop()


def mark(notification_id: int, read: bool) -> None:
    try:
        with session_scope() as db:
            ns.set_read(db, profile.id, notification_id, read)
    except Exception as exc:  # noqa: BLE001
        st.session_state["_notif_error"] = str(exc)


def mark_all() -> None:
    with session_scope() as db:
        count = ns.mark_all_read(db, profile.id)
    st.session_state["_flash"] = [("success", f"Marked {count} notification(s) as read.")]


if st.session_state.pop("_notif_error", None):
    st.error("That notification could not be updated.")

top1, top2 = st.columns([3, 1])
top1.markdown(f"**{unread}** unread")
top2.button("Mark all as read", disabled=unread == 0, on_click=mark_all, key="notif_mark_all")

if not items:
    empty_state("You are all caught up" if view == "Unread" else "No notifications",
                "Alerts appear when a budget reaches 80% or 100%, a savings goal hits a milestone or a deadline gets close, "
                "or a spending pattern changes noticeably.", "🔔")
for n in items:
    with st.container(border=True):
        state = "" if n.is_read else "  **(new)**"
        resolved = "" if n.is_active else "  *(no longer applies)*"
        st.markdown(f"{SEVERITY_ICON.get(n.severity, '🔵')} **{escape_md(n.title)}**{state}{resolved}")
        st.markdown(escape_md(n.message))
        st.caption(f"{format_datetime(n.created_at, profile.date_format)}")
        b1, b2, _ = st.columns([1, 2, 4])
        b1.button("Mark unread" if n.is_read else "Mark read", on_click=mark, args=(n.id, not n.is_read), key=f"nread_{n.id}")
        dest = DESTINATIONS.get(n.ref_type or "")
        if dest:
            with b2:
                page_link(*dest)
