"""Plotly figure builders. Each returns ``None`` when there is nothing real to plot."""
from __future__ import annotations

from datetime import date

import plotly.graph_objects as go

from services import analytics_service as an
from services import budget_service as bs
from services.analytics_service import CategoryTotal, SeriesPoint
from utils.dates import month_label
from utils.formatting import currency_symbol
from utils.money import paise_to_float

PALETTE = ["#0f766e", "#2563eb", "#d97706", "#7c3aed", "#db2777", "#65a30d", "#0891b2", "#b91c1c", "#4b5563", "#ca8a04"]
INCOME, EXPENSE, NET = "#15803d", "#b91c1c", "#2563eb"
LEVEL_COLOR = {bs.OK: "#15803d", bs.WARNING: "#b45309", bs.EXCEEDED: "#b91c1c"}


def _finish(fig: go.Figure, title: str, currency: str, height: int = 360, money_axis: bool = True) -> go.Figure:
    fig.update_layout(title=dict(text=title, x=0, font=dict(size=16)), height=height, margin=dict(l=10, r=10, t=50, b=10),
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
    if money_axis:
        fig.update_yaxes(tickprefix=currency_symbol(currency), separatethousands=True, gridcolor="rgba(128,128,128,.25)", rangemode="tozero")
    return fig


def _bucket_label(d: date, granularity: str) -> str:
    if granularity == "month":
        return d.strftime("%b %Y")
    if granularity == "week":
        return "Week of " + d.strftime("%d %b")
    return d.strftime("%d %b")


def category_doughnut(items: list[CategoryTotal], currency: str, title: str, max_slices: int = 8) -> go.Figure | None:
    items = [i for i in items if i.total > 0]
    if not items:
        return None
    shown = items[:max_slices]
    rest = sum(i.total for i in items[max_slices:])
    labels = [i.name for i in shown] + (["Other categories"] if rest else [])
    values = [paise_to_float(i.total) for i in shown] + ([paise_to_float(rest)] if rest else [])
    sym = currency_symbol(currency)
    fig = go.Figure(go.Pie(labels=labels, values=values, hole=0.55, sort=False, marker=dict(colors=PALETTE),
                           textinfo="label+percent", textposition="outside",
                           hovertemplate=f"%{{label}}<br>{sym}%{{value:,.2f}}<br>%{{percent}}<extra></extra>"))
    return _finish(fig, title, currency, 400, money_axis=False)


def category_bars(items: list[CategoryTotal], currency: str, title: str, limit: int = 10) -> go.Figure | None:
    items = [i for i in items if i.total > 0][:limit]
    if not items:
        return None
    sym = currency_symbol(currency)
    fig = go.Figure(go.Bar(x=[paise_to_float(i.total) for i in reversed(items)], y=[i.name for i in reversed(items)], orientation="h",
                           marker_color=PALETTE[0], customdata=[i.share_pct for i in reversed(items)],
                           hovertemplate=f"%{{y}}<br>{sym}%{{x:,.2f}} (%{{customdata:.1f}}% of total)<extra></extra>"))
    fig = _finish(fig, title, currency, max(280, 40 * len(items) + 90), money_axis=False)
    fig.update_xaxes(tickprefix=sym, separatethousands=True, gridcolor="rgba(128,128,128,.25)")
    return fig


def income_expense_bars(points: list[SeriesPoint], granularity: str, currency: str, title: str) -> go.Figure | None:
    if not points or not any(p.income or p.expense for p in points):
        return None
    sym = currency_symbol(currency)
    x = [_bucket_label(p.period_start, granularity) for p in points]
    fig = go.Figure()
    fig.add_bar(name="Income", x=x, y=[paise_to_float(p.income) for p in points], marker_color=INCOME,
                hovertemplate=f"%{{x}}<br>Income {sym}%{{y:,.2f}}<extra></extra>")
    fig.add_bar(name="Expenses", x=x, y=[paise_to_float(p.expense) for p in points], marker_color=EXPENSE,
                hovertemplate=f"%{{x}}<br>Expenses {sym}%{{y:,.2f}}<extra></extra>")
    fig.update_layout(barmode="group")
    fig.update_xaxes(type="category")
    return _finish(fig, title, currency)


def expense_trend(points: list[SeriesPoint], currency: str, title: str) -> go.Figure | None:
    if not points or not any(p.expense for p in points):
        return None
    sym = currency_symbol(currency)
    fig = go.Figure(go.Scatter(x=[month_label(p.period_start.strftime("%Y-%m"), short=True) for p in points],
                               y=[paise_to_float(p.expense) for p in points], mode="lines+markers", line=dict(color=EXPENSE, width=3),
                               hovertemplate=f"%{{x}}<br>Expenses {sym}%{{y:,.2f}}<extra></extra>"))
    fig.update_xaxes(type="category")
    return _finish(fig, title, currency, 320)


def savings_trend(points: list[SeriesPoint], contributions: dict[str, int], currency: str, title: str) -> go.Figure | None:
    if not points or not any(p.income or p.expense for p in points):
        return None
    sym = currency_symbol(currency)
    keys = [p.period_start.strftime("%Y-%m") for p in points]
    x = [month_label(k, short=True) for k in keys]
    fig = go.Figure()
    fig.add_bar(name="Recorded surplus (income - expenses)", x=x, y=[paise_to_float(p.net) for p in points],
                marker_color=[NET if p.net >= 0 else EXPENSE for p in points],
                hovertemplate=f"%{{x}}<br>Surplus {sym}%{{y:,.2f}}<extra></extra>")
    if any(contributions.get(k) for k in keys):
        fig.add_scatter(name="Goal contributions", x=x, y=[paise_to_float(contributions.get(k, 0)) for k in keys],
                        mode="lines+markers", line=dict(color=PALETTE[0], width=3),
                        hovertemplate=f"%{{x}}<br>Goal contributions {sym}%{{y:,.2f}}<extra></extra>")
    fig.update_xaxes(type="category")
    fig = _finish(fig, title, currency, 340)
    fig.update_yaxes(rangemode="normal", zeroline=True, zerolinecolor="rgba(128,128,128,.6)")
    return fig


def budget_vs_actual(statuses: list[bs.BudgetStatus], currency: str, title: str) -> go.Figure | None:
    if not statuses:
        return None
    sym = currency_symbol(currency)
    labels = [s.label for s in statuses]
    fig = go.Figure()
    fig.add_bar(name="Budget limit", x=labels, y=[paise_to_float(s.limit) for s in statuses], marker_color="rgba(100,116,139,.55)",
                hovertemplate=f"%{{x}}<br>Limit {sym}%{{y:,.2f}}<extra></extra>")
    fig.add_bar(name="Spent", x=labels, y=[paise_to_float(s.used) for s in statuses], marker_color=[LEVEL_COLOR[s.level] for s in statuses],
                customdata=[s.utilization for s in statuses],
                hovertemplate=f"%{{x}}<br>Spent {sym}%{{y:,.2f}} (%{{customdata:.1f}}% of limit)<extra></extra>")
    fig.update_layout(barmode="group")
    fig.update_xaxes(type="category")
    return _finish(fig, title, currency)


def budget_history(rows: list[tuple[str, bs.BudgetStatus | None]], currency: str, title: str) -> go.Figure | None:
    rows = [(m, s) for m, s in rows if s is not None]
    if not rows:
        return None
    sym = currency_symbol(currency)
    x = [month_label(m, short=True) for m, _ in rows]
    fig = go.Figure()
    fig.add_bar(name="Overall budget", x=x, y=[paise_to_float(s.limit) for _, s in rows], marker_color="rgba(100,116,139,.55)",
                hovertemplate=f"%{{x}}<br>Budget {sym}%{{y:,.2f}}<extra></extra>")
    fig.add_bar(name="Spent", x=x, y=[paise_to_float(s.used) for _, s in rows], marker_color=[LEVEL_COLOR[s.level] for _, s in rows],
                customdata=[s.utilization for _, s in rows],
                hovertemplate=f"%{{x}}<br>Spent {sym}%{{y:,.2f}} (%{{customdata:.1f}}%)<extra></extra>")
    fig.update_layout(barmode="group")
    fig.update_xaxes(type="category")
    return _finish(fig, title, currency, 320)


def payment_method_chart(rows: list[tuple[str, int, int]], currency: str, title: str) -> go.Figure | None:
    if not rows:
        return None
    sym = currency_symbol(currency)
    fig = go.Figure(go.Bar(x=[m for m, _, _ in rows], y=[paise_to_float(t) for _, _, t in rows], marker_color=PALETTE[1],
                           customdata=[n for _, n, _ in rows],
                           hovertemplate=f"%{{x}}<br>{sym}%{{y:,.2f}} across %{{customdata}} transaction(s)<extra></extra>"))
    fig.update_xaxes(type="category")
    return _finish(fig, title, currency, 320)


def comparison_bars(rows: list[tuple[str, an.Comparison]], currency: str, title: str, limit: int = 8) -> go.Figure | None:
    rows = [(n, c) for n, c in rows if c.current or c.previous][:limit]
    if not rows:
        return None
    sym = currency_symbol(currency)
    names = [n for n, _ in rows]
    fig = go.Figure()
    fig.add_bar(name="Previous period", x=names, y=[paise_to_float(c.previous) for _, c in rows], marker_color="rgba(100,116,139,.55)",
                hovertemplate=f"%{{x}}<br>Previous {sym}%{{y:,.2f}}<extra></extra>")
    fig.add_bar(name="Selected period", x=names, y=[paise_to_float(c.current) for _, c in rows], marker_color=PALETTE[0],
                hovertemplate=f"%{{x}}<br>Selected {sym}%{{y:,.2f}}<extra></extra>")
    fig.update_layout(barmode="group")
    fig.update_xaxes(type="category")
    return _finish(fig, title, currency)
