"""Period summaries and CSV exports.

Every export is scoped to one user and built only from that user's rows. Text
cells are neutralised against spreadsheet formula injection (a cell starting
with = + - @ would otherwise be executed by Excel). Passwords, hashes and
session data are never part of an export.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

import config
from models import GoalContribution, SavingsGoal, User
from services import analytics_service as an
from services import budget_service as bs
from services import transaction_service as ts
from utils.dates import month_key, months_range
from utils.errors import ValidationError
from utils.money import paise_to_decimal

_FORMULA_CHARS = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value: object) -> str:
    """Prefix risky text with an apostrophe so spreadsheets treat it as plain text."""
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(_FORMULA_CHARS) else text


def amount_str(paise: int) -> str:
    """Plain decimal with two places, e.g. '1250.50' (no symbol or separators, so it stays numeric)."""
    return f"{paise_to_decimal(paise):.2f}"


def _to_bytes(rows: list[list[str]]) -> bytes:
    buf = io.StringIO(newline="")
    csv.writer(buf, lineterminator="\r\n").writerows(rows)
    return buf.getvalue().encode("utf-8-sig")  # BOM makes Excel read UTF-8 correctly


def _currency(db: Session, user_id: int) -> str:
    return db.scalar(select(User.currency).where(User.id == user_id)) or config.DEFAULT_CURRENCY


def export_transactions_csv(db: Session, user_id: int, filters: ts.TransactionFilters | None = None,
                            sort_by: str = "date") -> tuple[bytes, int]:
    """Return ``(csv_bytes, number_of_rows)`` for the user's transactions matching ``filters``."""
    rows, total = ts.query_transactions(db, user_id, filters, sort_by=sort_by, descending=True)
    cur = _currency(db, user_id)
    out = [["Transaction ID", "Date", "Type", "Category", f"Amount ({cur})", "Payment Method", "Description"]]
    for r in rows:
        out.append([str(r.id), r.txn_date.isoformat(), r.type.capitalize(), safe_cell(r.category_name),
                    amount_str(r.amount), r.payment_method, safe_cell(r.description)])
    return _to_bytes(out), total


def export_budgets_csv(db: Session, user_id: int, first_month: str, last_month: str) -> tuple[bytes, int]:
    cur = _currency(db, user_id)
    out = [["Month", "Scope", f"Budget Limit ({cur})", f"Spent ({cur})", f"Remaining ({cur})", "Utilization %", "Status"]]
    count = 0
    for month in months_range(first_month, last_month):
        for s in bs.month_statuses(db, user_id, month):
            out.append([month, safe_cell(s.label), amount_str(s.limit), amount_str(s.used), amount_str(s.remaining),
                        "" if s.utilization is None else f"{s.utilization:.2f}", bs.LEVEL_LABELS[s.level]])
            count += 1
    return _to_bytes(out), count


def export_contributions_csv(db: Session, user_id: int) -> tuple[bytes, int]:
    cur = _currency(db, user_id)
    rows = db.execute(
        select(GoalContribution, SavingsGoal.name).join(
            SavingsGoal, (SavingsGoal.id == GoalContribution.goal_id) & (SavingsGoal.user_id == GoalContribution.user_id))
        .where(GoalContribution.user_id == user_id).order_by(GoalContribution.contribution_date.desc(), GoalContribution.id.desc())
    ).all()
    out = [["Contribution ID", "Goal", "Date", f"Amount ({cur})", "Note"]]
    for c, goal_name in rows:
        out.append([str(c.id), safe_cell(goal_name), c.contribution_date.isoformat(), amount_str(c.amount), safe_cell(c.note)])
    return _to_bytes(out), len(rows)


@dataclass(frozen=True)
class PeriodSummary:
    start: date
    end: date
    currency: str
    income: int
    expense: int
    net: int
    savings_rate: float | None
    expense_categories: list[an.CategoryTotal]
    income_categories: list[an.CategoryTotal]
    budgets: list[bs.BudgetStatus]       # budgets of every month the period touches (full-month figures)
    transaction_count: int


def build_summary(db: Session, user_id: int, start: date, end: date) -> PeriodSummary:
    if end < start:
        raise ValidationError("End date must not be before the start date.")
    totals = an.period_totals(db, user_id, start, end)
    _, count = ts.query_transactions(db, user_id, ts.TransactionFilters(start=start, end=end), limit=1)
    budgets: list[bs.BudgetStatus] = []
    for month in months_range(month_key(start), month_key(end)):
        budgets += bs.month_statuses(db, user_id, month)
    return PeriodSummary(start, end, _currency(db, user_id), totals.income, totals.expense, totals.net, totals.savings_rate,
                         an.category_totals(db, user_id, start, end, config.EXPENSE),
                         an.category_totals(db, user_id, start, end, config.INCOME), budgets, count)


def summary_csv(summary: PeriodSummary) -> bytes:
    cur = summary.currency
    rows: list[list[str]] = [["Section", "Item", f"Amount ({cur})", "Detail"]]
    rows.append(["Period", "Start", "", summary.start.isoformat()])
    rows.append(["Period", "End", "", summary.end.isoformat()])
    rows.append(["Totals", "Total income", amount_str(summary.income), ""])
    rows.append(["Totals", "Total expenses", amount_str(summary.expense), ""])
    sign = "-" if summary.net < 0 else ""
    rows.append(["Totals", "Net cash flow", f"{sign}{amount_str(abs(summary.net))}", "Income minus expenses for this period"])
    rows.append(["Totals", "Savings rate", "", "Not available (no income)" if summary.savings_rate is None else f"{summary.savings_rate:.2f}%"])
    rows.append(["Totals", "Transactions", "", str(summary.transaction_count)])
    for c in summary.expense_categories:
        rows.append(["Expenses by category", safe_cell(c.name), amount_str(c.total), "" if c.share_pct is None else f"{c.share_pct:.2f}% of expenses"])
    for c in summary.income_categories:
        rows.append(["Income by category", safe_cell(c.name), amount_str(c.total), "" if c.share_pct is None else f"{c.share_pct:.2f}% of income"])
    for s in summary.budgets:
        rows.append(["Budgets", safe_cell(f"{s.month} {s.label}"), amount_str(s.limit),
                     f"Spent {amount_str(s.used)}; remaining {'-' if s.remaining < 0 else ''}{amount_str(abs(s.remaining))}; {bs.LEVEL_LABELS[s.level]}"])
    return _to_bytes(rows)
