"""Reusable UI building blocks: page header, metric cards, bars, empty states, flash messages."""
from __future__ import annotations

import streamlit as st

from services import budget_service as bs
from services.auth_service import UserProfile
from utils.formatting import escape_html, format_money, format_pct

# Restrained palette. Status is always conveyed with text and a symbol as well as colour.
TONES = {
    "neutral": "#64748b", "accent": "#0f766e", "positive": "#15803d",
    "negative": "#b91c1c", "warning": "#b45309", "info": "#2563eb",
}
LEVEL_TONE = {bs.OK: "positive", bs.WARNING: "warning", bs.EXCEEDED: "negative"}
LEVEL_BADGE = {bs.OK: "✔ On track", bs.WARNING: "▲ Approaching limit", bs.EXCEEDED: "✖ Limit reached or exceeded"}
SEVERITY_ICON = {"critical": "🔴", "warning": "🟠", "info": "🔵", "success": "🟢"}

_CSS = """
<style>
:root {
  --sf-ink: #172033;
  --sf-muted: #64748b;
  --sf-teal: #0f766e;
  --sf-line: #e2e8f0;
  --sf-surface: #ffffff;
  --sf-page: #f5f8fc;
}

/* Main canvas */
[data-testid="stAppViewContainer"] { background: var(--sf-page); }
.block-container {
  max-width: 1480px !important;
  padding: 1.7rem 2.2rem 3rem !important;
}
[data-testid="stHeader"] { background: rgba(245,248,252,.9); }
.sf-title {
  color: var(--sf-ink); font-size: 2rem; font-weight: 750;
  letter-spacing: -.035em; line-height: 1.2; margin: .15rem 0 .35rem;
}
.sf-sub { color: var(--sf-muted); margin: 0 0 1.35rem; font-size: .98rem; }

/* Sidebar and navigation */
[data-testid="stSidebar"] {
  background: #111c2e; border-right: 1px solid #243248;
}
[data-testid="stSidebar"] > div:first-child { background: #111c2e; }
[data-testid="stSidebar"] [data-testid="stSidebarNav"] { padding-top: .7rem; }
[data-testid="stSidebar"] [data-testid="stSidebarNavLink"] {
  color: #dbe5f2; border-radius: 10px; margin: 3px 8px;
  padding: .52rem .7rem; transition: background .15s ease;
}
[data-testid="stSidebar"] [data-testid="stSidebarNavLink"]:hover {
  background: #24354b; color: #ffffff;
}
[data-testid="stSidebar"] [data-testid="stSidebarNavLink"][aria-current="page"] {
  background: #0f766e; color: #ffffff; font-weight: 650;
}
[data-testid="stSidebar"] .stMarkdown,
[data-testid="stSidebar"] .stCaption,
[data-testid="stSidebar"] p { color: #d1dbea; }
[data-testid="stSidebar"] hr { border-color: #34445b; }
[data-testid="stSidebar"] .stButton > button {
  border: 1px solid #53657e; color: #f8fafc; background: #1b2a40;
}
[data-testid="stSidebar"] .stButton > button:hover {
  border-color: #5eead4; color: #ffffff; background: #243a50;
}
.sf-brand {
  display: flex; align-items: center; gap: .7rem; margin: .9rem .15rem .85rem;
}
.sf-brand-mark {
  display: grid; place-items: center; width: 2.35rem; height: 2.35rem;
  border-radius: 11px; background: #0f766e; color: #ffffff;
  font-weight: 800; font-size: 1.2rem;
}
.sf-brand-name { color: #ffffff; font-weight: 750; font-size: 1.02rem; line-height: 1.2; }
.sf-brand-caption { color: #9fb0c7; font-size: .67rem; letter-spacing: .12em; margin-top: .15rem; }
.sf-user-card {
  border: 1px solid #34445b; background: #1a2a40; border-radius: 12px;
  padding: .7rem .8rem; margin: .6rem 0 .8rem;
}
.sf-user-label { color: #9fb0c7; font-size: .68rem; letter-spacing: .09em; }
.sf-user-name { color: #ffffff; font-size: .92rem; font-weight: 650; margin-top: .16rem; }

/* Metric and information cards */
.sf-card {
  border: 1px solid var(--sf-line); border-left: 4px solid var(--tone);
  border-radius: 14px; padding: 1rem 1.05rem; background: var(--sf-surface);
  box-shadow: 0 3px 12px rgba(15,23,42,.045); min-height: 108px;
  height: 100%; margin-bottom: .65rem;
}
.sf-card .lbl { color: var(--sf-muted); font-size: .86rem; font-weight: 550; }
.sf-card .val { color: var(--sf-ink); font-size: 1.58rem; font-weight: 760; line-height: 1.35; word-break: break-word; margin-top: .18rem; }
.sf-card .sub { color: var(--sf-muted); font-size: .78rem; margin-top: .18rem; }
.sf-bar { height: 10px; border-radius: 8px; background: #e8eef5; overflow: hidden; margin: .38rem 0 .18rem; }
.sf-bar > span { display: block; height: 100%; border-radius: 8px; }
.sf-row { display: flex; justify-content: space-between; gap: .75rem; flex-wrap: wrap; font-size: .95rem; }
.sf-muted { color: var(--sf-muted); font-size: .84rem; }
.sf-badge { font-weight: 650; font-size: .84rem; }
.sf-empty {
  border: 1px dashed #cbd5e1; border-radius: 13px; padding: 1.15rem 1.25rem;
  margin: .55rem 0 1rem; background: rgba(255,255,255,.8); color: var(--sf-ink);
}
.sf-empty b { font-size: 1.02rem; }
.sf-note { border-left: 4px solid #94a3b8; border-radius: 0 8px 8px 0; padding: .45rem .85rem; margin: .5rem 0 .9rem; font-size: .88rem; background: #eef3f8; color: #475569; }

/* Tabs, forms and controls */
div[data-testid="stForm"] {
  border: 1px solid var(--sf-line); border-radius: 14px;
  padding: 1.05rem 1.15rem; background: #ffffff;
  box-shadow: 0 2px 10px rgba(15,23,42,.035);
}
div[data-testid="stTabs"] [data-baseweb="tab-list"] {
  gap: .4rem; border-bottom: 1px solid var(--sf-line);
}
div[data-testid="stTabs"] button[role="tab"] {
  border-radius: 9px 9px 0 0; padding: .65rem 1rem; font-weight: 600;
}
div[data-testid="stTabs"] button[role="tab"][aria-selected="true"] {
  color: var(--sf-teal); border-bottom: 3px solid var(--sf-teal);
}
.stButton > button, [data-testid="stFormSubmitButton"] button,
[data-testid="stDownloadButton"] button {
  border-radius: 9px; font-weight: 650; min-height: 2.45rem;
  transition: transform .12s ease, box-shadow .12s ease;
}
.stButton > button:hover, [data-testid="stFormSubmitButton"] button:hover,
[data-testid="stDownloadButton"] button:hover {
  transform: translateY(-1px); box-shadow: 0 4px 12px rgba(15,118,110,.13);
}
[data-baseweb="input"] > div, [data-baseweb="select"] > div,
[data-testid="stDateInput"] input, [data-testid="stTextArea"] textarea {
  border-radius: 9px;
}
[data-testid="stDataFrame"] {
  border: 1px solid var(--sf-line); border-radius: 12px; overflow: hidden;
}
div[data-testid="stAlert"] { border-radius: 11px; }
hr { border-color: var(--sf-line); }

/* Light motion and a more polished app canvas */
@keyframes sfPageEnter {
  from { opacity: 0; transform: translateY(8px); }
  to   { opacity: 1; transform: translateY(0); }
}
@keyframes sfCardEnter {
  from { opacity: 0; transform: translateY(7px) scale(.992); }
  to   { opacity: 1; transform: translateY(0) scale(1); }
}
[data-testid="stMainBlockContainer"] { animation: sfPageEnter .38s ease-out both; }
.sf-title { position: relative; padding-left: .85rem; }
.sf-title:before {
  content: ""; position: absolute; left: 0; top: .12em; bottom: .12em;
  width: 4px; border-radius: 5px; background: linear-gradient(180deg,#14b8a6,#60a5fa);
}
.sf-card {
  position: relative; overflow: hidden;
  background: linear-gradient(145deg,#ffffff 0%,#fbfdff 100%);
  animation: sfCardEnter .42s ease-out both;
  transition: transform .18s ease, box-shadow .18s ease, border-color .18s ease;
}
.sf-card:hover {
  transform: translateY(-3px);
  box-shadow: 0 10px 24px rgba(15,23,42,.09);
  border-color: #cbd5e1;
}
.sf-card:after {
  content: ""; position: absolute; width: 100px; height: 100px; right: -42px; top: -48px;
  border-radius: 50%; background: color-mix(in srgb, var(--tone) 9%, transparent);
  pointer-events: none;
}
[data-testid="stPlotlyChart"], [data-testid="stDataFrame"],
[data-testid="stVerticalBlockBorderWrapper"] {
  border-radius: 14px; transition: box-shadow .18s ease, transform .18s ease;
}
[data-testid="stPlotlyChart"]:hover,
[data-testid="stVerticalBlockBorderWrapper"]:hover {
  box-shadow: 0 7px 20px rgba(15,23,42,.055);
}
[data-testid="stSidebar"] [data-testid="stSidebarNavLink"] { transition: all .18s ease; }
[data-testid="stSidebar"] [data-testid="stSidebarNavLink"]:hover { transform: translateX(3px); }
.stButton > button, [data-testid="stFormSubmitButton"] button,
[data-testid="stDownloadButton"] button { transition: all .18s ease; }
.stButton > button:active, [data-testid="stFormSubmitButton"] button:active { transform: scale(.985); }
[data-baseweb="input"]:focus-within, [data-baseweb="select"]:focus-within {
  box-shadow: 0 0 0 2px rgba(20,184,166,.16); border-radius: 10px;
}
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation-duration: .01ms !important; transition-duration: .01ms !important; }
}
@media (max-width: 800px) {
  .block-container { padding: 1.1rem 1rem 2rem !important; }
  .sf-title { font-size: 1.65rem; }
  .sf-card .val { font-size: 1.35rem; }
}
</style>
"""


def inject_css() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)


def page_header(title: str, subtitle: str | None = None) -> None:
    st.markdown(f'<div class="sf-title">{escape_html(title)}</div>', unsafe_allow_html=True)
    if subtitle:
        st.markdown(f'<div class="sf-sub">{escape_html(subtitle)}</div>', unsafe_allow_html=True)


def setup_page(title: str, subtitle: str | None = None) -> UserProfile:
    """Standard start of every protected page: styles, login check, header, flash messages."""
    from components.navigation import require_login
    inject_css()
    profile = require_login()
    page_header(title, subtitle)
    show_flash()
    return profile


def metric_card(label: str, value: str, sub: str = "", tone: str = "neutral") -> None:
    st.markdown(
        f'<div class="sf-card" style="--tone:{TONES.get(tone, TONES["neutral"])}">'
        f'<div class="lbl">{escape_html(label)}</div><div class="val">{escape_html(value)}</div>'
        f'<div class="sub">{escape_html(sub)}</div></div>', unsafe_allow_html=True)


def progress_bar(pct: float | None, tone: str = "accent") -> None:
    width = max(0.0, min(100.0, pct or 0.0))
    st.markdown(f'<div class="sf-bar"><span style="width:{width:.1f}%;background:{TONES.get(tone, TONES["accent"])}"></span></div>',
                unsafe_allow_html=True)


def budget_bar(status: bs.BudgetStatus, profile: UserProfile) -> None:
    """One budget: label, bar, amounts and a text status (never colour alone)."""
    cur = profile.currency
    tone = LEVEL_TONE[status.level]
    left = (f"Exceeded by {format_money(status.exceeded_by, cur)}" if status.exceeded_by
            else f"Remaining {format_money(status.remaining, cur)}")
    st.markdown(
        f'<div class="sf-row"><b>{escape_html(status.label)}</b>'
        f'<span class="sf-badge" style="color:{TONES[tone]}">{LEVEL_BADGE[status.level]} - {escape_html(format_pct(status.utilization, 1))}</span></div>',
        unsafe_allow_html=True)
    progress_bar(status.utilization, tone)
    st.markdown(f'<div class="sf-row sf-muted"><span>Spent {escape_html(format_money(status.used, cur))} of '
                f'{escape_html(format_money(status.limit, cur))}</span><span>{escape_html(left)}</span></div>', unsafe_allow_html=True)


def empty_state(title: str, message: str, icon: str = "📭") -> None:
    st.markdown(f'<div class="sf-empty">{icon} <b>{escape_html(title)}</b><br>{escape_html(message)}</div>', unsafe_allow_html=True)


def note(text: str) -> None:
    st.markdown(f'<div class="sf-note">{escape_html(text)}</div>', unsafe_allow_html=True)


def flash(kind: str, message: str) -> None:
    """Queue a message to show after the next rerun (kind: success/error/warning/info)."""
    st.session_state.setdefault("_flash", []).append((kind, message))


def show_flash() -> None:
    for kind, message in st.session_state.pop("_flash", []):
        getattr(st, kind if kind in ("success", "error", "warning", "info") else "info")(message)


def page_link(page: str, label: str, icon: str | None = None) -> None:
    """Link to another page; degrades to plain text if the page is not registered."""
    try:
        st.page_link(page, label=label, icon=icon)
    except Exception:  # noqa: BLE001 - e.g. page not in the navigation (tests, direct runs)
        st.caption(f"{icon or ''} {label}".strip())


def show_table(df, **kwargs):
    """Full-width table that works on old and new Streamlit versions."""
    try:
        return st.dataframe(df, width="stretch", **kwargs)
    except TypeError:
        return st.dataframe(df, use_container_width=True, **kwargs)


def show_chart(fig, key: str) -> None:
    st.plotly_chart(fig, key=key)


def friendly_error(exc: Exception) -> None:
    """Show a safe message for expected errors; log and hide details of unexpected ones."""
    import logging
    from utils.errors import ServiceError
    if isinstance(exc, ServiceError):
        st.error(str(exc))
    else:
        logging.getLogger("ui").exception("Unexpected error")
        st.error("Something went wrong and your change was not saved. Please try again. "
                 "If it keeps happening, check data/app.log.")
