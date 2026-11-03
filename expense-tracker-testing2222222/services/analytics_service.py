"""Read-only financial calculations used by the dashboard, analytics and reports.

Every figure is computed from the transactions table on demand (there are no
stored counters that could drift out of sync), using integer paise.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

import config
from models import Category, Transaction, User
from utils.dates import bucket_start, bucket_starts, month_bounds, month_key, months_range
from utils.errors import ValidationError
from utils.money import percentage


# ------------------------------------------------------------------ value objects
@dataclass(frozen=True)
class PeriodTotals:
    income: int
    expense: int

    @property
    def net(self) -> int:
        """Net cash flow = income - expenses (may be negative)."""
        return self.income - self.expense

    @property
    def savings_rate(self) -> float | None:
        return savings_rate(self.income, self.expense)


@dataclass(frozen=True)
class CategoryTotal:
    category_id: int
    name: str
    total: int
    share_pct: float | None  # share of the grand total for the same type/period


@dataclass(frozen=True)
class SeriesPoint:
    period_start: date
    income: int
    expense: int

    @property
    def net(self) -> int:
        return self.income - self.expense


@dataclass(frozen=True)
class Comparison:
    current: int
    previous: int
    change: int
    change_pct: float | None
    available: bool          # False when there is no valid baseline
    reason: str | None = None


# ------------------------------------------------------------------ pure helpers
def signed_pct(numerator: int, denominator: int) -> float | None:
    """Percentage that keeps its sign (``percentage`` alone only handles non-negative input)."""
    value = percentage(abs(numerator), denominator)
    if value is None:
        return None
    return -value if numerator < 0 else value


def savings_rate(income: int, expense: int) -> float | None:
    """(income - expense) / income * 100, or ``None`` when income is zero."""
    return signed_pct(income - expense, income) if income > 0 else None


def compare(current: int, previous: int) -> Comparison:
    """Absolute and percentage change; percentage is unavailable for a zero baseline."""
    change = current - previous
    if previous <= 0:
        return Comparison(current, previous, change, None, False,
                          "The previous period has no recorded spending, so a percentage change cannot be calculated.")
    return Comparison(current, previous, change, signed_pct(change, previous), True)


def auto_granularity(start: date, end: date) -> str:
    days = (end - start).days + 1
    return "day" if days <= 45 else ("week" if days <= 180 else "month")


# ------------------------------------------------------------------ queries
def _date_filters(stmt, start: date | None, end: date | None):
    if start is not None:
        stmt = stmt.where(Transaction.txn_date >= start)
    if end is not None:
        stmt = stmt.where(Transaction.txn_date <= end)
    return stmt


def _check_range(start: date | None, end: date | None) -> None:
    if start and end and end < start:
        raise ValidationError("End date must not be before the start date.")


def period_totals(db: Session, user_id: int, start: date | None = None, end: date | None = None) -> PeriodTotals:
    _check_range(start, end)
    stmt = select(Transaction.type, func.coalesce(func.sum(Transaction.amount), 0)).where(Transaction.user_id == user_id)
    stmt = _date_filters(stmt, start, end).group_by(Transaction.type)
    sums = {t: int(s) for t, s in db.execute(stmt)}
    return PeriodTotals(sums.get(config.INCOME, 0), sums.get(config.EXPENSE, 0))


def current_balance(db: Session, user_id: int) -> int:
    """Opening balance + all recorded income - all recorded expenses.

    The opening balance is added exactly once and is never an income record.
    """
    opening = db.scalar(select(User.opening_balance).where(User.id == user_id)) or 0
    totals = period_totals(db, user_id)
    return int(opening) + totals.income - totals.expense


def category_totals(
    db: Session, user_id: int, start: date | None, end: date | None, txn_type: str = config.EXPENSE
) -> list[CategoryTotal]:
    _check_range(start, end)
    total_sum = func.sum(Transaction.amount)
    stmt = (select(Category.id, Category.name, total_sum)
            .join(Category, Category.id == Transaction.category_id)
            .where(Transaction.user_id == user_id, Transaction.type == txn_type))
    stmt = _date_filters(stmt, start, end).group_by(Category.id, Category.name).order_by(total_sum.desc(), Category.name)
    rows = [(cid, name, int(total)) for cid, name, total in db.execute(stmt)]
    grand = sum(t for _, _, t in rows)
    return [CategoryTotal(cid, name, total, percentage(total, grand)) for cid, name, total in rows]


def expenses_by_category(db: Session, user_id: int, start: date, end: date) -> tuple[int, dict[int, int]]:
    """Total expenses and a ``{category_id: amount}`` map for a period."""
    items = category_totals(db, user_id, start, end, config.EXPENSE)
    return sum(i.total for i in items), {i.category_id: i.total for i in items}


def monthly_category_spending(db: Session, user_id: int, first_month: str, last_month: str) -> dict[int, dict[str, int]]:
    """``{category_id: {month: amount}}`` of expenses between two month keys (inclusive)."""
    start, end = month_bounds(first_month)[0], month_bounds(last_month)[1]
    month_expr = func.strftime("%Y-%m", Transaction.txn_date)
    stmt = (select(Transaction.category_id, month_expr, func.sum(Transaction.amount))
            .where(Transaction.user_id == user_id, Transaction.type == config.EXPENSE,
                   Transaction.txn_date >= start, Transaction.txn_date <= end)
            .group_by(Transaction.category_id, month_expr))
    out: dict[int, dict[str, int]] = defaultdict(dict)
    for cid, month, total in db.execute(stmt):
        out[cid][month] = int(total)
    return dict(out)


def series(db: Session, user_id: int, start: date, end: date, granularity: str = "month") -> list[SeriesPoint]:
    """Income/expense per day, week (Mon-start) or month, zero-filled for empty buckets."""
    if granularity not in ("day", "week", "month"):
        raise ValidationError("Granularity must be day, week or month.")
    _check_range(start, end)
    stmt = (select(Transaction.txn_date, Transaction.type, func.sum(Transaction.amount))
            .where(Transaction.user_id == user_id, Transaction.txn_date >= start, Transaction.txn_date <= end)
            .group_by(Transaction.txn_date, Transaction.type))
    buckets: dict[date, dict[str, int]] = {b: {config.INCOME: 0, config.EXPENSE: 0} for b in bucket_starts(start, end, granularity)}
    for d, t, total in db.execute(stmt):
        buckets[bucket_start(d, granularity)][t] += int(total)
    return [SeriesPoint(b, v[config.INCOME], v[config.EXPENSE]) for b, v in sorted(buckets.items())]


def monthly_series(db: Session, user_id: int, first_month: str, last_month: str) -> list[SeriesPoint]:
    start, end = month_bounds(first_month)[0], month_bounds(last_month)[1]
    return series(db, user_id, start, end, "month")


def first_transaction_date(db: Session, user_id: int) -> date | None:
    return db.scalar(select(func.min(Transaction.txn_date)).where(Transaction.user_id == user_id))


def payment_method_totals(db: Session, user_id: int, start: date | None, end: date | None,
                          txn_type: str | None = None) -> list[tuple[str, int, int]]:
    """``[(method, number_of_transactions, total_amount)]`` sorted by amount."""
    _check_range(start, end)
    total_sum = func.sum(Transaction.amount)
    stmt = select(Transaction.payment_method, func.count(), total_sum).where(Transaction.user_id == user_id)
    if txn_type:
        stmt = stmt.where(Transaction.type == txn_type)
    stmt = _date_filters(stmt, start, end).group_by(Transaction.payment_method).order_by(total_sum.desc())
    return [(m, int(n), int(t)) for m, n, t in db.execute(stmt)]


def average_monthly_category_spending(db: Session, user_id: int, start: date, end: date) -> list[tuple[str, int, int]]:
    """``[(category, average per calendar month, total)]`` for the months the range touches."""
    months = len(months_range(month_key(start), month_key(end)))
    return [(c.name, round(c.total / months), c.total) for c in category_totals(db, user_id, start, end)]


def compare_categories(
    db: Session, user_id: int, start: date, end: date, prev_start: date, prev_end: date
) -> list[tuple[str, Comparison]]:
    """Per-category expense comparison between two periods, largest change first."""
    cur = {c.category_id: c for c in category_totals(db, user_id, start, end)}
    prev = {c.category_id: c for c in category_totals(db, user_id, prev_start, prev_end)}
    out = []
    for cid in set(cur) | set(prev):
        name = (cur.get(cid) or prev[cid]).name
        out.append((name, compare(cur[cid].total if cid in cur else 0, prev[cid].total if cid in prev else 0)))
    return sorted(out, key=lambda item: abs(item[1].change), reverse=True)
